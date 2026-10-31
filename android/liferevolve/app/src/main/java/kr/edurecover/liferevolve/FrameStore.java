package kr.edurecover.liferevolve;

import android.content.Context;
import android.graphics.Bitmap;
import android.net.Uri;
import android.util.AtomicFile;
import org.json.JSONObject;
import java.io.*;
import java.util.*;

/** 취득 PNG와 취득 시각을 앱 내부에 보관합니다. 인식 정확도·문서 완성률을 만들지 않습니다. */
final class FrameStore {
    static File directory(Context c) { File d=new File(c.getFilesDir(),"screen-frames"); d.mkdirs(); return d; }
    static File resolve(Context c,String name) throws IOException {
        if (!CapturePolicy.validFrameName(name)) throw new IOException("올바르지 않은 화면 이름");
        File d=directory(c), f=new File(d,name);
        if (!f.getCanonicalFile().getParentFile().equals(d.getCanonicalFile())) throw new IOException("허용하지 않는 경로");
        return f;
    }
    static Uri uri(File f) { return Uri.parse("content://kr.edurecover.liferevolve.frames/"+f.getName()); }
    static List<File> list(Context c) {
        File[] files=directory(c).listFiles((d,n)->CapturePolicy.validFrameName(n));
        List<File> result=new ArrayList<>(files==null?Collections.emptyList():Arrays.asList(files));
        result.sort((a,b)->b.getName().compareTo(a.getName())); return result;
    }
    static synchronized File save(Context c,Bitmap b,String session,long imageTimestampNs,long receivedAtMs) throws Exception {
        List<File> before=list(c); long used=0; for(File f:before)used+=f.length();
        if(!CapturePolicy.canSave(before.size(),used,0)) throw new IOException("보관 한도입니다. 저장한 화면을 정리한 뒤 다시 시작하십시오.");
        String name=System.currentTimeMillis()+"-"+UUID.randomUUID().toString().substring(0,8)+".png";
        File finalFile=resolve(c,name), temp=new File(directory(c),name+".pending");
        boolean complete=false;
        try {
            try(FileOutputStream out=new FileOutputStream(temp)) {
                if(!b.compress(Bitmap.CompressFormat.PNG,100,out))throw new IOException("PNG 변환 실패");
                out.getFD().sync();
            }
            if(!CapturePolicy.canSave(before.size(),used,temp.length()))throw new IOException("128MB 보관 한도입니다. 화면을 정리하십시오.");
            if(!temp.renameTo(finalFile))throw new IOException("화면 파일 보관 실패");
            JSONObject meta=new JSONObject();meta.put("session",session);meta.put("imageTimestampNs",imageTimestampNs);meta.put("receivedAtMs",receivedAtMs);meta.put("committedAtMs",System.currentTimeMillis());
            meta.put("width",b.getWidth());meta.put("height",b.getHeight());meta.put("source","MediaProjection");
            meta.put("ocrExecuted",false);meta.put("wholeDocumentCoverageMeasured",false);
            AtomicFile af=new AtomicFile(new File(directory(c),name+".json"));
            FileOutputStream out=af.startWrite();
            try{out.write(meta.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));af.finishWrite(out);}
            catch(Exception ex){af.failWrite(out);finalFile.delete();throw ex;}
            complete=true;return finalFile;
        } finally {
            temp.delete();
            if(!complete){
                finalFile.delete();
                new File(directory(c),name+".json").delete();
                new File(directory(c),name+".json.new").delete();
                new File(directory(c),name+".json.bak").delete();
            }
        }
    }

    static void delete(Context c,File f) throws IOException {
        File target=resolve(c,f.getName());
        if(target.exists()&&!target.delete())throw new IOException("화면 파일을 삭제하지 못했습니다.");
        File meta=new File(target.getParentFile(),target.getName()+".json");
        if(meta.exists()&&!meta.delete())throw new IOException("화면은 삭제했으나 취득 기록을 삭제하지 못했습니다.");
    }
}
