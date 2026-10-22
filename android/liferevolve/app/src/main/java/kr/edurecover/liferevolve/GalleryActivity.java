package kr.edurecover.liferevolve;

import android.app.*;
import android.content.*;
import android.graphics.*;
import android.net.Uri;
import android.os.*;
import android.provider.MediaStore;
import android.view.*;
import android.widget.*;
import java.io.*;
import java.text.DateFormat;
import java.util.*;

/** PNG 원본을 확인·선택·저장·공유합니다. 작은 미리보기로 원본 파일을 덮어쓰지 않습니다. */
public final class GalleryActivity extends Activity {
    private LinearLayout root; private boolean selection; private File selected; private Bitmap preview;
    private final BroadcastReceiver receiver=new BroadcastReceiver(){@Override public void onReceive(Context c,Intent i){if(selected==null)showList();}};
    @Override public void onCreate(Bundle state){super.onCreate(state);selection=getIntent().getBooleanExtra("selection",false);root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(Color.rgb(255,247,235));
        root.setOnApplyWindowInsetsListener((view,insets)->{android.graphics.Insets b=insets.getInsets(WindowInsets.Type.systemBars());view.setPadding(b.left+12,b.top+12,b.right+12,b.bottom+12);return insets;});setContentView(root);showList();}
    private Button button(String text,View.OnClickListener listener){Button b=new Button(this);b.setText(text);b.setOnClickListener(listener);return b;}
    private TextView text(String text){TextView t=new TextView(this);t.setText(text);t.setTextSize(16);t.setPadding(8,10,8,10);return t;}
    private void clear(){root.removeAllViews();if(preview!=null){preview.recycle();preview=null;}}
    private void showList(){clear();selected=null;root.addView(text(selection?"플랫폼에 가져올 화면 선택":"취득한 화면 보관함"));root.addView(text(CaptureService.status(this)+"\n앱 전체 보관 한도 40장·128MB입니다. 문서 전체 확보율과 글자 정확도는 측정하지 않았습니다."));
        root.addView(button("새로고침",v->showList()));ScrollView scroll=new ScrollView(this);LinearLayout list=new LinearLayout(this);list.setOrientation(LinearLayout.VERTICAL);scroll.addView(list);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        List<File> files=FrameStore.list(this);if(files.isEmpty())list.addView(text("보관한 화면이 없습니다. 화면 취득을 허용한 뒤 대상 앱에서 스크롤하십시오."));
        for(File file:files){String title=DateFormat.getDateTimeInstance().format(new Date(file.lastModified()))+" · "+String.format(Locale.KOREA,"%.1f",file.length()/1048576d)+"MB";
            list.addView(button(title,v->{if(selection){Intent result=new Intent().setData(FrameStore.uri(file)).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);setResult(RESULT_OK,result);finish();}else showDetail(file);}));}
    }
    private void showDetail(File file){clear();selected=file;BitmapFactory.Options size=new BitmapFactory.Options();size.inJustDecodeBounds=true;BitmapFactory.decodeFile(file.getAbsolutePath(),size);
        root.addView(button("보관함 목록",v->showList()));root.addView(text("과거에 취득한 화면 · 현재 화면의 증거가 아닙니다.\n원본 "+size.outWidth+" × "+size.outHeight+" 화소 · PNG 보관"));
        ImageView image=new ImageView(this);image.setScaleType(ImageView.ScaleType.FIT_CENTER);image.setAdjustViewBounds(true);
        BitmapFactory.Options options=new BitmapFactory.Options();options.inSampleSize=1;
        while(size.outWidth/options.inSampleSize>1600||size.outHeight/options.inSampleSize>2200)options.inSampleSize*=2;
        preview=BitmapFactory.decodeFile(file.getAbsolutePath(),options);image.setImageBitmap(preview);root.addView(image,new LinearLayout.LayoutParams(-1,0,1));
        root.addView(button("갤러리에 PNG 저장",v->saveImage(file)));root.addView(button("다른 앱으로 공유",v->share(file)));
        root.addView(button("플랫폼에서 사용",v->{startActivity(new Intent(this,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP|Intent.FLAG_ACTIVITY_SINGLE_TOP).putExtra("openScreen",true));finish();}));
        root.addView(button("이 화면 삭제",v->new AlertDialog.Builder(this).setTitle("앱에 보관한 이 화면을 삭제합니까?").setMessage("이미 갤러리로 저장한 PNG는 삭제하지 않습니다.").setPositiveButton("삭제",(d,w)->{try{FrameStore.delete(this,file);showList();}catch(IOException ex){toast(ex.getMessage());}}).setNegativeButton("취소",null).show()));
    }
    private void share(File file){Uri uri=FrameStore.uri(file);Intent send=new Intent(Intent.ACTION_SEND).setType("image/png").putExtra(Intent.EXTRA_STREAM,uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);send.setClipData(ClipData.newUri(getContentResolver(),"취득한 화면",uri));try{startActivity(Intent.createChooser(send,"화면을 보낼 앱"));}catch(ActivityNotFoundException ex){toast("공유할 앱이 없습니다.");}}
    private void saveImage(File file){Uri uri=null;try{ContentValues values=new ContentValues();values.put(MediaStore.Images.Media.DISPLAY_NAME,"LifeRevolve-"+file.getName());values.put(MediaStore.Images.Media.MIME_TYPE,"image/png");values.put(MediaStore.Images.Media.RELATIVE_PATH,"Pictures/LifeRevolve");values.put(MediaStore.Images.Media.IS_PENDING,1);
            uri=getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI,values);if(uri==null)throw new IOException("저장 위치를 만들지 못했습니다.");
            try(InputStream in=new FileInputStream(file);OutputStream out=getContentResolver().openOutputStream(uri)){if(out==null)throw new IOException("저장 파일을 열지 못했습니다.");byte[] block=new byte[65536];int n;while((n=in.read(block))!=-1)out.write(block,0,n);}
            values.clear();values.put(MediaStore.Images.Media.IS_PENDING,0);getContentResolver().update(uri,values,null,null);toast("갤러리의 LifeRevolve 앨범에 PNG를 저장했습니다.");
        }catch(Exception ex){if(uri!=null)getContentResolver().delete(uri,null,null);toast("저장 실패: "+ex.getClass().getSimpleName());}}
    private void toast(String value){Toast.makeText(this,value,Toast.LENGTH_LONG).show();}
    @Override protected void onResume(){super.onResume();registerReceiver(receiver,new IntentFilter(CaptureService.ACTION_STATE),Context.RECEIVER_NOT_EXPORTED);}
    @Override protected void onPause(){unregisterReceiver(receiver);super.onPause();}
    @Override protected void onDestroy(){if(preview!=null)preview.recycle();super.onDestroy();}
    @Override public void onBackPressed(){if(selected!=null)showList();else super.onBackPressed();}
}
