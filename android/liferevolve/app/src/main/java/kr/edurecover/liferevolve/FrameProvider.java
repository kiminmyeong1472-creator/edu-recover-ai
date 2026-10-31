package kr.edurecover.liferevolve;

import android.content.*;
import android.database.*;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.*;

/** 선택한 PNG 한 개에만 읽기 권한을 부여합니다. 경로·쓰기 접근은 허용하지 않습니다. */
public final class FrameProvider extends ContentProvider {
    @Override public boolean onCreate(){return true;}
    private File file(Uri uri)throws FileNotFoundException{
        try{if(uri.getPathSegments().size()!=1)throw new IOException();File f=FrameStore.resolve(getContext(),uri.getLastPathSegment());if(!f.isFile())throw new IOException();return f;}
        catch(IOException ex){throw new FileNotFoundException("보관한 화면을 찾을 수 없습니다.");}
    }
    @Override public ParcelFileDescriptor openFile(Uri uri,String mode)throws FileNotFoundException{
        if(!"r".equals(mode))throw new FileNotFoundException("읽기만 허용합니다.");return ParcelFileDescriptor.open(file(uri),ParcelFileDescriptor.MODE_READ_ONLY);
    }
    @Override public Cursor query(Uri uri,String[] projection,String selection,String[] args,String order){
        try{File f=file(uri);String[] cols=projection==null?new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE}:projection;
            MatrixCursor cursor=new MatrixCursor(cols);Object[] values=new Object[cols.length];
            for(int i=0;i<cols.length;i++){if(OpenableColumns.DISPLAY_NAME.equals(cols[i]))values[i]=f.getName();else if(OpenableColumns.SIZE.equals(cols[i]))values[i]=f.length();}
            cursor.addRow(values);return cursor;
        }catch(FileNotFoundException ex){return null;}
    }
    @Override public String getType(Uri uri){return "image/png";}
    @Override public Uri insert(Uri u,ContentValues v){throw new UnsupportedOperationException();}
    @Override public int delete(Uri u,String s,String[] a){throw new UnsupportedOperationException();}
    @Override public int update(Uri u,ContentValues v,String s,String[] a){throw new UnsupportedOperationException();}
}
