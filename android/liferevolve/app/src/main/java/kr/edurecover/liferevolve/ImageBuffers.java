package kr.edurecover.liferevolve;
import java.nio.ByteBuffer;

/** ImageReader의 줄 끝 여백을 제거하되 실제 영상 화소는 축소하지 않습니다. */
final class ImageBuffers {
    static ByteBuffer packRgba(ByteBuffer source,int width,int height,int pixelStride,int rowStride) {
        if(!CapturePolicy.acceptableSize(width,height)||pixelStride!=4||rowStride<(long)width*4)
            throw new IllegalArgumentException("지원하지 않는 영상 크기·배열입니다.");
        int limit=source.limit(),base=source.position();
        long required=(long)base+(long)(height-1)*rowStride+(long)width*4;
        if(required>limit)throw new IllegalArgumentException("영상 배열이 불완전합니다.");
        ByteBuffer result=ByteBuffer.allocate(width*height*4),input=source.duplicate();
        for(int y=0;y<height;y++){int offset=base+y*rowStride;input.limit(limit);input.position(offset);input.limit(offset+width*4);result.put(input);}
        result.flip();return result;
    }
}
