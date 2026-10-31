package kr.edurecover.liferevolve;

import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.graphics.*;
import android.hardware.display.*;
import android.media.*;
import android.media.projection.*;
import android.os.*;
import android.util.DisplayMetrics;
import android.view.WindowManager;
import java.nio.ByteBuffer;
import java.util.UUID;

/** 공개 MediaProjection만 사용합니다. GPU 버퍼나 표시 직전 메모리에 직접 접근하지 않습니다. */
public final class CaptureService extends Service {
    static final String ACTION_STOP="kr.edurecover.liferevolve.STOP";
    static final String ACTION_STATE="kr.edurecover.liferevolve.STATE";
    private static final String CHANNEL="screen-capture";
    static volatile boolean running=false;
    static volatile boolean stopping=false;
    private HandlerThread thread; private Handler worker;
    private MediaProjection projection; private VirtualDisplay display; private ImageReader reader;
    private volatile boolean finished=false; private long lastSaved=0; private int saved=0;
    private int width,height,density; private String session;

    static String status(Context c){
        String value=c.getSharedPreferences("capture",0).getString("status","화면 취득 대기");
        if(!running&&(value.startsWith("취득 중")||value.startsWith("공유 허용 완료")||value.startsWith("화면 크기 변경")))
            return "화면 취득이 실행 중이 아닙니다. 저장한 화면은 보관함에서 확인하십시오.";
        return value;
    }
    private void publish(String text){getSharedPreferences("capture",0).edit().putString("status",text).apply();sendBroadcast(new Intent(ACTION_STATE).setPackage(getPackageName()));}
    @Override public void onCreate(){super.onCreate();thread=new HandlerThread("frame-collector");thread.start();worker=new Handler(thread.getLooper());}
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        if(intent==null){stopSelf();return START_NOT_STICKY;}
        if(ACTION_STOP.equals(intent.getAction())){stopping=true;publish("취득 종료 요청 · 마지막 화면 보관을 정리 중입니다.");worker.post(()->finish("취득 종료 · 저장한 화면 "+saved+"장"));return START_NOT_STICKY;}
        if(running||stopping||finished){publish("이미 취득 중입니다. 종료 후 새로 시작하십시오.");return START_NOT_STICKY;}
        Intent permission=intent.getParcelableExtra("permission",Intent.class);
        int code=intent.getIntExtra("resultCode",Activity.RESULT_CANCELED);
        if(permission==null||code!=Activity.RESULT_OK){publish("화면 공유 허용이 필요합니다.");stopSelf();return START_NOT_STICKY;}
        running=true;
        getSystemService(NotificationManager.class).createNotificationChannel(new NotificationChannel(CHANNEL,"허용한 화면 취득",NotificationManager.IMPORTANCE_LOW));
        Intent stop=new Intent(this,CaptureService.class).setAction(ACTION_STOP);
        PendingIntent pi=PendingIntent.getService(this,2,stop,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        PendingIntent open=PendingIntent.getActivity(this,1,new Intent(this,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        Notification n=new Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.ic_menu_camera).setContentTitle("생활의 혁명 · 화면 취득")
                .setContentText("2초 간격 · 최대 40장/128MB · 종료는 여기에서").setContentIntent(open).setOngoing(true)
                .addAction(new Notification.Action.Builder(null,"취득 종료",pi).build()).build();
        try{startForeground(23,n,ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);}
        catch(RuntimeException ex){running=false;publish("화면 취득 알림을 시작하지 못했습니다: "+ex.getClass().getSimpleName());stopSelf();return START_NOT_STICKY;}
        publish("공유 허용 완료 · 첫 화면 수신 대기");
        worker.post(()->startProjection(code,permission));
        return START_NOT_STICKY;
    }
    private void startProjection(int code,Intent permission){
        if(finished)return;
        try{
            session=UUID.randomUUID().toString();
            Rect bounds=getSystemService(WindowManager.class).getMaximumWindowMetrics().getBounds();
            width=bounds.width();height=bounds.height();density=getResources().getConfiguration().densityDpi;
            if(!CapturePolicy.acceptableSize(width,height))throw new IllegalStateException("화면 크기가 8백만 화소를 넘습니다. 축소하지 않고 중단했습니다.");
            projection=getSystemService(MediaProjectionManager.class).getMediaProjection(code,permission);
            projection.registerCallback(new MediaProjection.Callback(){
                @Override public void onStop(){finish("운영체제가 화면 공유를 종료했습니다. 저장한 화면은 보관함에 있습니다.");}
                @Override public void onCapturedContentResize(int w,int h){resize(w,h);}
                @Override public void onCapturedContentVisibilityChanged(boolean visible){if(!visible)publish("공유 대상이 보이지 않습니다. 영상의 현재성·문서 전체 확보율은 판정하지 않습니다.");}
            },worker);
            reader=newReader(width,height);
            display=projection.createVirtualDisplay("LifeRevolve-authorized-screen",width,height,density,DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,reader.getSurface(),null,worker);
        }catch(Exception ex){finish("취득 시작 실패: "+message(ex));}
    }
    private ImageReader newReader(int w,int h){
        ImageReader r=ImageReader.newInstance(w,h,PixelFormat.RGBA_8888,2);
        r.setOnImageAvailableListener(this::onImage,worker);return r;
    }
    private void resize(int w,int h){
        if(finished||display==null||(width==w&&height==h))return;
        if(!CapturePolicy.acceptableSize(w,h)){finish("접힘·회전 후 화면이 8백만 화소를 넘었습니다. 원본 크기를 유지하기 위해 중단했습니다.");return;}
        try{
            density=getResources().getConfiguration().densityDpi;
            ImageReader old=reader;
            ImageReader next=newReader(w,h);
            display.setSurface(null);display.resize(w,h,density);display.setSurface(next.getSurface());
            reader=next;width=w;height=h;old.close();lastSaved=0;
            publish("화면 크기 변경: "+w+" × "+h+" · 새 크기로 취득 대기");
        }catch(Exception ex){finish("화면 크기 변경 처리 실패: "+message(ex));}
    }
    private void onImage(ImageReader source){
        Image image=null;Bitmap bitmap=null;
        try{
            if(finished||source!=reader)return;
            image=source.acquireLatestImage();if(image==null)return;
            long receivedAtMs=System.currentTimeMillis();
            long now=SystemClock.elapsedRealtime();if(lastSaved!=0&&now-lastSaved<CapturePolicy.FRAME_INTERVAL_MS)return;
            int w=image.getWidth(),h=image.getHeight();
            if(!CapturePolicy.acceptableSize(w,h))throw new IllegalStateException("허용 크기를 넘는 화면입니다.");
            Image.Plane plane=image.getPlanes()[0];int pixel=plane.getPixelStride(),row=plane.getRowStride();
            ByteBuffer packed=ImageBuffers.packRgba(plane.getBuffer(),w,h,pixel,row);
            bitmap=Bitmap.createBitmap(w,h,Bitmap.Config.ARGB_8888);bitmap.copyPixelsFromBuffer(packed);
            FrameStore.save(this,bitmap,session,image.getTimestamp(),receivedAtMs);saved++;lastSaved=now;
            publish("취득 중 · 이번 실행 "+saved+"장 · "+w+" × "+h+" · OCR·AI 분석은 실행하지 않았습니다.");
            if(FrameStore.list(this).size()>=CapturePolicy.MAX_FRAMES)finish("보관 한도 40장에 도달해 종료했습니다. 화면을 저장하거나 정리하십시오.");
        }catch(OutOfMemoryError error){finish("화면을 원본 크기로 처리할 메모리가 부족해 종료했습니다.");}
        catch(Exception ex){finish("화면 보관 실패: "+message(ex));}
        finally{if(bitmap!=null)bitmap.recycle();if(image!=null)image.close();}
    }
    private String message(Exception e){String text=e.getMessage();return text==null?e.getClass().getSimpleName():text;}
    private void finish(String reason){
        if(finished)return;finished=true;stopping=true;
        if(display!=null){display.release();display=null;}
        if(reader!=null){reader.close();reader=null;}
        if(projection!=null){MediaProjection p=projection;projection=null;p.stop();}
        publish(reason);stopForeground(STOP_FOREGROUND_REMOVE);stopSelf();
    }
    @Override public void onDestroy(){worker.post(()->{
        if(!finished)finish("취득 서비스가 종료됐습니다. 저장한 화면은 보관함에 있습니다.");
        running=false;stopping=false;sendBroadcast(new Intent(ACTION_STATE).setPackage(getPackageName()));thread.quitSafely();
    });super.onDestroy();}
    @Override public IBinder onBind(Intent i){return null;}
}
