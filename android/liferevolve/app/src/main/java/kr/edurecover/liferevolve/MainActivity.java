package kr.edurecover.liferevolve;

import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.projection.MediaProjectionManager;
import android.net.Uri;
import android.os.*;
import android.view.*;
import android.webkit.*;
import android.widget.*;

/** 원격 JavaScript bridge 없이 공식 플랫폼을 열고 공개 화면 공유 기능을 제공합니다. */
public final class MainActivity extends Activity {
    static final int REQUEST_CAPTURE=100, REQUEST_PICK=101, REQUEST_NOTIFICATIONS=102;
    private WebView web; private TextView status; private ValueCallback<Uri[]> fileCallback;
    private boolean starting=false,waitingForStop=false; private LinearLayout root;
    private final BroadcastReceiver receiver=new BroadcastReceiver(){@Override public void onReceive(Context c,Intent i){status.setText(CaptureService.status(c));if(waitingForStop&&!CaptureService.running&&!CaptureService.stopping){waitingForStop=false;openGallery(false);}}};
    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(Color.rgb(255,247,235));root.setPadding(10,0,10,0);
        root.setOnApplyWindowInsetsListener((view,insets)->{android.graphics.Insets b=insets.getInsets(WindowInsets.Type.systemBars());view.setPadding(b.left+10,b.top,b.right+10,b.bottom);return insets;});
        LinearLayout bar=new LinearLayout(this);bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.addView(button("화면 취득 시작",v->startCapture()),new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1));
        bar.addView(button("종료·결과",v->{if(CaptureService.running||CaptureService.stopping){waitingForStop=true;status.setText("취득 종료 요청 · 마지막 화면 보관을 정리 중입니다.");requestStop();}else openGallery(false);}),new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1));
        bar.addView(button("플랫폼",v->web.loadUrl(BuildConfig.PLATFORM_ORIGIN+"/life")),new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1));
        root.addView(bar);status=new TextView(this);status.setTextSize(12);status.setPadding(8,3,8,8);root.addView(status);
        web=new WebView(this);root.addView(web,new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,0,1));setContentView(root);
        WebSettings settings=web.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);settings.setAllowContentAccess(false);settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportMultipleWindows(false);settings.setMediaPlaybackRequiresUserGesture(true);settings.setSafeBrowsingEnabled(true);
        CookieManager.getInstance().setAcceptCookie(true);CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
        web.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
                String url=request.getUrl().toString();if(CapturePolicy.trustedUrl(url,BuildConfig.PLATFORM_ORIGIN))return false;
                if(request.isForMainFrame()&&request.hasGesture()&&"https".equals(request.getUrl().getScheme())){
                    try{startActivity(new Intent(Intent.ACTION_VIEW,request.getUrl()));}catch(ActivityNotFoundException ignored){toast("링크를 열 브라우저가 없습니다.");}
                }
                return true;
            }
            @Override public void onReceivedError(WebView view,WebResourceRequest request,WebResourceError error){if(request.isForMainFrame())status.setText("플랫폼 연결 실패 · 네트워크를 확인한 뒤 ‘플랫폼’을 선택하십시오.");}
            @Override public void onReceivedHttpError(WebView view,WebResourceRequest request,WebResourceResponse response){if(request.isForMainFrame()&&response.getStatusCode()>=400)status.setText("플랫폼 응답 오류 "+response.getStatusCode()+" · 화면 취득·보관함은 별도입니다.");}
            @Override public void onPageFinished(WebView view,String url){CookieManager.getInstance().flush();}
        });
        web.setDownloadListener((url,userAgent,contentDisposition,mimeType,contentLength)->{
            if(!CapturePolicy.trustedUrl(url,BuildConfig.PLATFORM_ORIGIN)){toast("공식 플랫폼 밖의 다운로드 주소는 열지 않습니다.");return;}
            new AlertDialog.Builder(MainActivity.this).setTitle("브라우저에서 파일 다운로드")
                    .setMessage("공식 플랫폼의 파일을 브라우저에서 엽니다. 브라우저에서 로그인을 다시 요청할 수 있습니다.")
                    .setPositiveButton("브라우저로 열기",(d,w)->{try{startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url)));}catch(ActivityNotFoundException ex){toast("다운로드할 브라우저가 없습니다.");}})
                    .setNegativeButton("취소",null).show();
        });
        web.setWebChromeClient(new WebChromeClient(){
            @Override public boolean onShowFileChooser(WebView view,ValueCallback<Uri[]> callback,FileChooserParams params){
                if(!CapturePolicy.trustedUrl(view.getUrl(),BuildConfig.PLATFORM_ORIGIN)){callback.onReceiveValue(null);return true;}
                if(fileCallback!=null)fileCallback.onReceiveValue(null);fileCallback=callback;
                new AlertDialog.Builder(MainActivity.this).setTitle("플랫폼에 가져올 사진")
                        .setItems(new String[]{"취득한 화면 보관함","갤러리·파일 앱"},(d,which)->{
                            if(which==0)openGallery(true);else{
                                Intent pick=new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("image/*").addCategory(Intent.CATEGORY_OPENABLE);
                                try{startActivityForResult(pick,REQUEST_PICK);}catch(ActivityNotFoundException ex){finishFile(null);toast("사진을 선택할 앱이 없습니다.");}
                            }
                        }).setOnCancelListener(d->finishFile(null)).show();return true;
            }
        });
        if(state==null||web.restoreState(state)==null)web.loadUrl(BuildConfig.PLATFORM_ORIGIN+"/life");
        handleIntent(getIntent());
    }
    private Button button(String title,View.OnClickListener listener){Button b=new Button(this);b.setText(title);b.setTextSize(12);b.setOnClickListener(listener);return b;}
    private void startCapture(){
        if(starting||CaptureService.running||CaptureService.stopping){toast("취득을 시작했거나 실행 중입니다. 먼저 종료하십시오.");return;}
        if(FrameStore.list(this).size()>=CapturePolicy.MAX_FRAMES){toast("보관 한도 40장입니다. 보관함에서 내보내거나 정리하십시오.");return;}
        starting=true;status.setText("운영체제의 화면 공유 허용을 기다립니다.");
        try{startActivityForResult(getSystemService(MediaProjectionManager.class).createScreenCaptureIntent(),REQUEST_CAPTURE);}
        catch(RuntimeException ex){starting=false;status.setText("화면 공유 요청 실패: "+ex.getClass().getSimpleName());}
    }
    private void requestStop(){if(CaptureService.running&&!CaptureService.stopping)startService(new Intent(this,CaptureService.class).setAction(CaptureService.ACTION_STOP));}
    private void openGallery(boolean selection){Intent i=new Intent(this,GalleryActivity.class).putExtra("selection",selection);if(selection)startActivityForResult(i,REQUEST_PICK);else startActivity(i);}
    private void finishFile(Uri uri){if(fileCallback!=null){fileCallback.onReceiveValue(uri==null?null:new Uri[]{uri});fileCallback=null;}}
    @Override protected void onActivityResult(int request,int result,Intent data){
        super.onActivityResult(request,result,data);
        if(request==REQUEST_CAPTURE){starting=false;
            if(result==RESULT_OK&&data!=null){status.setText("공유 허용 완료 · 첫 영상 수신 전입니다.");
                try{startForegroundService(new Intent(this,CaptureService.class).putExtra("resultCode",result).putExtra("permission",data));
                    toast("허용한 앱으로 이동해 천천히 스크롤하십시오. 돌아와 ‘종료·결과’를 선택하십시오.");
                }catch(RuntimeException ex){status.setText("화면 취득 시작 실패: "+ex.getClass().getSimpleName());}
            }else status.setText("화면 공유를 허용하지 않아 시작하지 않았습니다.");
        }else if(request==REQUEST_PICK){Uri uri=result==RESULT_OK&&data!=null?data.getData():null;if(uri!=null&&!("content".equals(uri.getScheme())))uri=null;finishFile(uri);}
    }
    private void handleIntent(Intent intent){if(intent!=null&&intent.getBooleanExtra("openScreen",false)){web.loadUrl(BuildConfig.PLATFORM_ORIGIN+"/life/screen");toast("플랫폼의 ‘사진 선택’에서 ‘취득한 화면 보관함’을 선택하십시오.");}}
    @Override protected void onNewIntent(Intent i){super.onNewIntent(i);setIntent(i);handleIntent(i);}
    @Override protected void onSaveInstanceState(Bundle out){web.saveState(out);super.onSaveInstanceState(out);}
    @Override protected void onResume(){super.onResume();registerReceiver(receiver,new IntentFilter(CaptureService.ACTION_STATE),Context.RECEIVER_NOT_EXPORTED);status.setText(CaptureService.status(this));if(waitingForStop&&!CaptureService.running&&!CaptureService.stopping){waitingForStop=false;openGallery(false);}}
    @Override protected void onPause(){unregisterReceiver(receiver);CookieManager.getInstance().flush();super.onPause();}
    @Override protected void onDestroy(){finishFile(null);if(web!=null){web.stopLoading();web.destroy();}super.onDestroy();}
    @Override public void onBackPressed(){if(web.canGoBack())web.goBack();else super.onBackPressed();}
    private void toast(String s){Toast.makeText(this,s,Toast.LENGTH_LONG).show();}
}
