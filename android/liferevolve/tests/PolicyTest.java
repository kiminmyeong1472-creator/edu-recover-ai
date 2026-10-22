package kr.edurecover.liferevolve;
import java.nio.ByteBuffer;
import java.util.Arrays;
public class PolicyTest {
    static int count=0;
    static void check(boolean value,String what){count++;if(!value)throw new AssertionError(what);}
    static void rejects(Runnable call,String what){boolean rejected=false;try{call.run();}catch(IllegalArgumentException expected){rejected=true;}check(rejected,what);}
    public static void main(String[] args){
        check(CapturePolicy.acceptableSize(1080,2520),"Galaxy cover size");
        check(CapturePolicy.acceptableSize(2208,1840),"fold size");
        check(!CapturePolicy.acceptableSize(0,2520),"zero rejected");
        check(!CapturePolicy.acceptableSize(Integer.MAX_VALUE,4),"overflow rejected");
        check(!CapturePolicy.acceptableSize(4096,4096),"over eight million rejected");
        String origin="https://edurecover-prod-production.up.railway.app";
        check(CapturePolicy.trustedUrl(origin+"/life/auth/login-form",origin),"same origin login");
        check(CapturePolicy.trustedUrl(origin+":443/life",origin),"default HTTPS port");
        check(!CapturePolicy.trustedUrl("http://edurecover-prod-production.up.railway.app/life",origin),"HTTP denied");
        check(!CapturePolicy.trustedUrl(origin+".evil.test/life",origin),"suffix host denied");
        check(!CapturePolicy.trustedUrl("https://attacker@edurecover-prod-production.up.railway.app/life",origin),"userinfo denied");
        check(!CapturePolicy.trustedUrl("javascript:alert(1)",origin),"script denied");
        check(!CapturePolicy.trustedUrl("content://kr.edurecover.liferevolve.frames/file",origin),"content navigation denied");
        check(!CapturePolicy.trustedUrl(origin+":444/life",origin),"other port denied");
        check(CapturePolicy.canSave(39,100,100),"last valid slot");
        check(!CapturePolicy.canSave(40,100,100),"count budget");
        check(!CapturePolicy.canSave(1,CapturePolicy.MAX_SESSION_BYTES-1,2),"byte budget");
        check(!CapturePolicy.canSave(1,Long.MAX_VALUE,1),"byte arithmetic overflow");
        check(CapturePolicy.validFrameName("1792612345678-0123abcd.png"),"frame ID allowed");
        check(!CapturePolicy.validFrameName("../../private.png"),"path traversal denied");
        check(!CapturePolicy.validFrameName("1792612345678-0123abcd.png.json"),"metadata not exported");
        byte[] raw={1,2,3,4,5,6,7,8,99,99,99,99,9,10,11,12,13,14,15,16};
        ByteBuffer input=ByteBuffer.wrap(raw),packed=ImageBuffers.packRgba(input,2,2,4,12);
        check(Arrays.equals(packed.array(),new byte[]{1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16}),"row padding excluded, last row without padding retained");
        check(input.position()==0&&input.limit()==raw.length,"source state unchanged");
        rejects(()->ImageBuffers.packRgba(ByteBuffer.wrap(new byte[19]),2,2,4,12),"incomplete last row");
        rejects(()->ImageBuffers.packRgba(ByteBuffer.wrap(new byte[30]),2,2,3,12),"unexpected pixel stride");
        rejects(()->ImageBuffers.packRgba(ByteBuffer.wrap(new byte[30]),2,2,4,7),"row stride too small");
        ByteBuffer offset=ByteBuffer.wrap(new byte[]{99,1,2,3,4});offset.position(1);
        check(Arrays.equals(ImageBuffers.packRgba(offset,1,1,4,4).array(),new byte[]{1,2,3,4}),"plane start position");
        System.out.println("PASS "+count+" meaningful assertions");
    }
}
