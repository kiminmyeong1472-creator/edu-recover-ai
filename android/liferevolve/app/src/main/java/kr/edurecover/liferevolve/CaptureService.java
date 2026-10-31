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
import android.view.*;
import android.widget.TextView;
import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.UUID;
import java.util.concurrent.*;

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
    // 연속 모드(톡 할 때만 한 장): 떠 있는 단추를 두 번 톡 → 시작, 한 번 톡 → 다음 화면, 길게 → 종료
    private boolean tapMode=false; private Bitmap latest; private long latestAt=0;
    private final BurstGesture gesture=new BurstGesture(); private final BurstUploader uploader=new BurstUploader();
    private final ExecutorService net=Executors.newSingleThreadExecutor(); private volatile String burstId;
    private View overlay; private Handler main; private int sent=0;

    static String status(Context c){
        String value=c.getSharedPreferences("capture",0).getString("status","화면 취득 대기");
        if(!running&&(value.startsWith("취득 중")||value.startsWith("공유 허용 완료")||value.startsWith("화면 크기 변경")))
            return "화면 취득이 실행 중이 아닙니다. 저장한 화면은 보관함에서 확인하십시오.";
        return value;
    }
    private void publish(String text){getSharedPreferences("capture",0).edit().putString("status",text).apply();sendBroadcast(new Intent(ACTION_STATE).setPackage(getPackageName()));}
    @Override public void onCreate(){super.onCreate();thread=new HandlerThread("frame-collector");thread.start();worker=new Handler(thread.getLooper());main=new Handler(Looper.getMainLooper());}
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        if(intent==null){stopSelf();return START_NOT_STICKY;}
        if(ACTION_STOP.equals(intent.getAction())){stopping=true;publish("취득 종료 요청 · 마지막 화면 보관을 정리 중입니다.");worker.post(()->finish("취득 종료 · 저장한 화면 "+saved+"장"));return START_NOT_STICKY;}
        if(running||stopping||finished){publish("이미 취득 중입니다. 종료 후 새로 시작하십시오.");return START_NOT_STICKY;}
        Intent permission=intent.getParcelableExtra("permission",Intent.class);
        int code=intent.getIntExtra("resultCode",Activity.RESULT_CANCELED);
        if(permission==null||code!=Activity.RESULT_OK){publish("화면 공유 허용이 필요합니다.");stopSelf();return START_NOT_STICKY;}
        running=true;tapMode=intent.getBooleanExtra("tapMode",false);
        getSystemService(NotificationManager.class).createNotificationChannel(new NotificationChannel(CHANNEL,"허용한 화면 취득",NotificationManager.IMPORTANCE_LOW));
        Intent stop=new Intent(this,CaptureService.class).setAction(ACTION_STOP);
        PendingIntent pi=PendingIntent.getService(this,2,stop,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        PendingIntent open=PendingIntent.getActivity(this,1,new Intent(this,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        Notification n=new Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.ic_menu_camera).setContentTitle("생활의 혁명 · 화면 취득")
                .setContentText(tapMode?"연속 모드 · 단추 두 번 톡 시작 · 톡마다 한 장 · 길게 누르면 끝":"2초 간격 · 최대 40장/128MB · 종료는 여기에서").setContentIntent(open).setOngoing(true)
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
                @Override public void onCapturedContentVisibilityChanged(boolean visible){contentVisible=visible;if(!visible)publish("공유 대상이 보이지 않습니다. 영상의 현재성·문서 전체 확보율은 판정하지 않습니다.");}
            },worker);
            reader=newReader(width,height);
            if(tapMode)main.post(this::showOverlay);
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
            if(tapMode){keepLatest(image,receivedAtMs);return;}
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
    /** 연속 모드에서는 저장하지 않고 가장 최근 화면 한 장만 메모리에 둡니다. */
    private void keepLatest(Image image,long at){
        int w=image.getWidth(),h=image.getHeight();
        if(!CapturePolicy.acceptableSize(w,h))return;
        Image.Plane plane=image.getPlanes()[0];
        ByteBuffer packed=ImageBuffers.packRgba(plane.getBuffer(),w,h,plane.getPixelStride(),plane.getRowStride());
        if(latest==null||latest.getWidth()!=w||latest.getHeight()!=h){if(latest!=null)latest.recycle();latest=Bitmap.createBitmap(w,h,Bitmap.Config.ARGB_8888);}
        latest.copyPixelsFromBuffer(packed);latestAt=at;
    }
    private volatile boolean contentVisible=true;
    /** 화면 전체에서 고르게 64×64 점을 뽑아 모두 검으면 캡처 방지 화면으로 봅니다. */
    private static boolean isProtected(Bitmap bmp){
        int n=64,w=bmp.getWidth(),h=bmp.getHeight();int[] s=new int[n*n];
        for(int j=0;j<n;j++)for(int i=0;i<n;i++)s[j*n+i]=bmp.getPixel(Math.min(w-1,i*w/n+w/(2*n)),Math.min(h-1,j*h/n+h/(2*n)));
        return CapturePolicy.looksProtected(s);
    }
    private void showOverlay(){
        if(overlay!=null||finished)return;
        if(!android.provider.Settings.canDrawOverlays(this)){publish("다른 앱 위에 표시 권한이 없어 연속 단추를 띄우지 못했습니다.");return;}
        TextView b=new TextView(this);b.setText("톡");b.setTextSize(18);b.setTextColor(Color.WHITE);b.setGravity(Gravity.CENTER);
        b.setBackgroundColor(Color.argb(150,173,79,50));
        WindowManager.LayoutParams lp=new WindowManager.LayoutParams(140,140,WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE|WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,PixelFormat.TRANSLUCENT);
        lp.gravity=Gravity.END|Gravity.CENTER_VERTICAL;
        final long[] down={0};
        b.setOnTouchListener((v,e)->{
            if(e.getAction()==MotionEvent.ACTION_DOWN)down[0]=SystemClock.uptimeMillis();
            else if(e.getAction()==MotionEvent.ACTION_UP){
                long t=SystemClock.uptimeMillis();boolean longPress=t-down[0]>=600;
                float x=e.getRawX(),y=e.getRawY();worker.post(()->onTap(t,x,y,longPress));
            }
            return true;
        });
        getSystemService(WindowManager.class).addView(b,lp);overlay=b;
        publish("연속 모드 준비 · 단추를 두 번 톡 하면 시작합니다.");
    }
    private void hideOverlay(){if(overlay!=null){View v=overlay;overlay=null;try{getSystemService(WindowManager.class).removeView(v);}catch(RuntimeException ignored){}}}
    private void onTap(long t,float x,float y,boolean longPress){
        BurstGesture.Action a=gesture.onTap(t,x,y,longPress);
        if(a==BurstGesture.Action.OPEN){
            sent=0;net.submit(()->{try{burstId=uploader.open(t);}catch(Exception ex){burstId=null;publish("연속 시작 실패: "+message(ex));}});
            capture(gesture.seq(),t);worker.postDelayed(this::idleCheck,1000);
        }else if(a==BurstGesture.Action.FRAME)capture(gesture.seq(),t);
        else if(a==BurstGesture.Action.CLOSE)closeBurst("길게 눌러 끝냄");
    }
    private void idleCheck(){
        if(finished||!gesture.isOpen())return;
        if(gesture.tick(SystemClock.uptimeMillis())==BurstGesture.Action.CLOSE){closeBurst("5초 동안 톡이 없어 끝냄");return;}
        worker.postDelayed(this::idleCheck,1000);
    }
    private void closeBurst(String why){
        String id=burstId;int n=sent;
        net.submit(()->{try{if(id!=null)uploader.close(id);}catch(Exception ignored){}});
        publish("연속 끝 · "+why+" · 올린 화면 "+n+"장 · 다시 두 번 톡 하면 새로 시작");
    }
    /** 단추를 잠깐 숨겨 단추가 찍히지 않게 하고, 숨긴 뒤 들어온 화면을 한 장 올립니다. */
    private void capture(int seq,long tapMs){
        View v=overlay;main.post(()->{if(v!=null)v.setVisibility(View.INVISIBLE);});
        long hiddenAt=System.currentTimeMillis();
        worker.postDelayed(()->{
            main.post(()->{if(v!=null)v.setVisibility(View.VISIBLE);});
            if(latest==null){publish(seq+"번 화면이 아직 없습니다. 빠진 번호로 남습니다.");return;}
            if(latestAt<hiddenAt)publish(seq+"번: 화면 변화가 없어 직전 화면으로 올립니다.");
            if(!contentVisible||isProtected(latest)){
                net.submit(()->{String id=burstId;try{if(id!=null)uploader.protectedScreen(id,seq,tapMs);}catch(Exception ignored){}});
                publish(seq+"번: 캡처가 막힌 화면이라 보내지 않았습니다. 막힌 사실만 기록합니다.");return;
            }
            ByteArrayOutputStream out=new ByteArrayOutputStream();latest.compress(Bitmap.CompressFormat.PNG,100,out);
            byte[] png=out.toByteArray();
            net.submit(()->{
                String id=burstId;
                if(id==null){publish(seq+"번: 연속이 시작되지 않아 올리지 못했습니다.");return;}
                try{String sha=uploader.frame(id,seq,tapMs,png);if(sha!=null){sent++;publish("연속 중 · "+seq+"번 올림 · 확인값 "+sha.substring(0,8));}}
                catch(Exception ex){publish(seq+"번 올리기 실패(빠진 번호로 남음): "+message(ex));}
            });
        },220);
    }
    private String message(Exception e){String text=e.getMessage();return text==null?e.getClass().getSimpleName():text;}
    private void finish(String reason){
        if(finished)return;finished=true;stopping=true;
        if(display!=null){display.release();display=null;}
        if(reader!=null){reader.close();reader=null;}
        if(projection!=null){MediaProjection p=projection;projection=null;p.stop();}
        if(tapMode){main.post(this::hideOverlay);if(gesture.isOpen())closeBurst("취득 종료");net.shutdown();if(latest!=null){latest.recycle();latest=null;}}
        publish(reason);stopForeground(STOP_FOREGROUND_REMOVE);stopSelf();
    }
    @Override public void onDestroy(){worker.post(()->{
        if(!finished)finish("취득 서비스가 종료됐습니다. 저장한 화면은 보관함에 있습니다.");
        running=false;stopping=false;sendBroadcast(new Intent(ACTION_STATE).setPackage(getPackageName()));thread.quitSafely();
    });super.onDestroy();}
    @Override public IBinder onBind(Intent i){return null;}
}
