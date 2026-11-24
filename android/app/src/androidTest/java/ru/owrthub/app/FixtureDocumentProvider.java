package ru.owrthub.app;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

/** Disposable documents in the test APK only; never included in the delivered app. */
public final class FixtureDocumentProvider extends ContentProvider {
    @Override public boolean onCreate(){return true;}
    private File file(Uri uri) throws FileNotFoundException {
        String name=uri.getLastPathSegment();
        if(name==null||!name.matches("(backup\\.tar\\.gz|corrupt\\.tar\\.gz|config\\.sh|upload\\.txt|download\\.txt|photo\\.png)"))throw new FileNotFoundException();
        return new File(getContext().getCacheDir(),"audit-"+name);
    }
    @Override public ParcelFileDescriptor openFile(Uri uri,String mode)throws FileNotFoundException{
        return ParcelFileDescriptor.open(file(uri),ParcelFileDescriptor.parseMode(mode));
    }
    @Override public String getType(Uri uri){return uri.toString().endsWith(".png")?"image/png":uri.toString().endsWith(".sh")?"text/plain":"application/gzip";}
    @Override public Cursor query(Uri uri,String[] projection,String selection,String[] args,String sort){
        try{
            File f=file(uri);String[] cols=projection==null?new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE}:projection;
            MatrixCursor out=new MatrixCursor(cols);Object[] values=new Object[cols.length];
            for(int i=0;i<cols.length;i++)values[i]=cols[i].equals(OpenableColumns.SIZE)?f.length():f.getName();
            out.addRow(values);return out;
        }catch(FileNotFoundException invalid){return null;}
    }
    @Override public Uri insert(Uri uri,ContentValues values){throw new UnsupportedOperationException();}
    @Override public int update(Uri uri,ContentValues values,String where,String[] args){return 0;}
    @Override public int delete(Uri uri,String where,String[] args){try{return file(uri).delete()?1:0;}catch(Exception e){return 0;}}
}
