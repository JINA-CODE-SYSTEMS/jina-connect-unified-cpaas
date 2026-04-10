"""
Media converters for WhatsApp template uploads.
Automatically converts files to WhatsApp-compatible formats.
"""
import os
import io
import subprocess
import tempfile
import logging
from typing import Optional, Tuple, BinaryIO
from django.core.files.uploadedfile import InMemoryUploadedFile, TemporaryUploadedFile
from PIL import Image

logger = logging.getLogger(__name__)


class ConversionError(Exception):
    """Raised when media conversion fails."""
    pass


class MediaConverter:
    """
    Converts media files to WhatsApp-compatible formats.
    
    Conversions supported:
    - Images: HEIC/HEIF → JPEG, WebP → PNG, CMYK → RGB, resize large images
    - Videos: MOV/AVI/MKV/WebM → MP4 (H.264)
    - Audio: WAV/FLAC/M4A → OGG (Opus) or MP3
    - Documents: Compress large PDFs
    """
    
    # Conversion mappings
    IMAGE_CONVERT_MAP = {
        '.heic': '.jpg',
        '.heif': '.jpg',
        '.webp': '.png',
        '.bmp': '.png',
        '.tiff': '.png',
        '.tif': '.png',
        '.gif': '.png',  # Static GIF to PNG
    }
    
    VIDEO_CONVERT_MAP = {
        '.mov': '.mp4',
        '.avi': '.mp4',
        '.mkv': '.mp4',
        '.webm': '.mp4',
        '.wmv': '.mp4',
        '.flv': '.mp4',
        '.m4v': '.mp4',
        '.3gp': '.mp4',
    }
    
    AUDIO_CONVERT_MAP = {
        '.wav': '.ogg',
        '.flac': '.ogg',
        '.m4a': '.mp3',
        '.aac': '.mp3',
        '.wma': '.mp3',
    }
    
    @classmethod
    def needs_conversion(cls, filename: str) -> Tuple[bool, str, Optional[str]]:
        """
        Check if a file needs conversion.
        
        Returns:
            Tuple of (needs_conversion, media_type, target_extension)
        """
        ext = os.path.splitext(filename.lower())[1]
        
        if ext in cls.IMAGE_CONVERT_MAP:
            return True, 'image', cls.IMAGE_CONVERT_MAP[ext]
        elif ext in cls.VIDEO_CONVERT_MAP:
            return True, 'video', cls.VIDEO_CONVERT_MAP[ext]
        elif ext in cls.AUDIO_CONVERT_MAP:
            return True, 'audio', cls.AUDIO_CONVERT_MAP[ext]
        
        return False, 'unknown', None
    
    @classmethod
    def convert(cls, file, target_format: Optional[str] = None, 
                max_size_mb: Optional[float] = None,
                is_template: bool = True) -> Tuple[BinaryIO, str, str]:
        """
        Convert a file to WhatsApp-compatible format.
        
        Args:
            file: The uploaded file object
            target_format: Optional target format override
            max_size_mb: Optional max size in MB (will compress if exceeded)
            is_template: If True, use stricter template limits
            
        Returns:
            Tuple of (converted_file, new_filename, mime_type)
        """
        filename = file.name.lower() if hasattr(file, 'name') else 'unknown'
        ext = os.path.splitext(filename)[1]
        
        needs_conv, media_type, target_ext = cls.needs_conversion(filename)
        
        if target_format:
            target_ext = target_format if target_format.startswith('.') else f'.{target_format}'
        
        if media_type == 'image' or ext in ['.jpg', '.jpeg', '.png']:
            return cls.convert_image(file, target_ext, max_size_mb, is_template)
        elif media_type == 'video' or ext in ['.mp4']:
            return cls.convert_video(file, target_ext, max_size_mb, is_template)
        elif media_type == 'audio' or ext in ['.ogg', '.mp3', '.amr']:
            return cls.convert_audio(file, target_ext, max_size_mb)
        else:
            # No conversion needed, return as-is
            file.seek(0)
            return file, filename, None
    
    @classmethod
    def convert_image(cls, file, target_ext: Optional[str] = None,
                      max_size_mb: Optional[float] = None,
                      is_template: bool = True) -> Tuple[io.BytesIO, str, str]:
        """
        Convert image to WhatsApp-compatible format.
        
        Features:
        - Convert HEIC/HEIF/WebP/BMP/TIFF to JPEG/PNG
        - Convert CMYK to RGB
        - Remove transparency from JPEG
        - Resize if too large
        - Compress to fit size limits
        """
        from PIL import Image, ExifTags
        
        filename = file.name if hasattr(file, 'name') else 'image.jpg'
        original_ext = os.path.splitext(filename.lower())[1]
        
        # Determine target format
        if not target_ext:
            if original_ext in cls.IMAGE_CONVERT_MAP:
                target_ext = cls.IMAGE_CONVERT_MAP[original_ext]
            elif original_ext in ['.png']:
                target_ext = '.png'
            else:
                target_ext = '.jpg'
        
        # Set size limit
        if max_size_mb is None:
            max_size_mb = 5 if is_template else 16
        max_size_bytes = int(max_size_mb * 1024 * 1024)
        
        try:
            file.seek(0)
            img = Image.open(file)
            
            # Handle EXIF orientation
            try:
                for orientation in ExifTags.TAGS.keys():
                    if ExifTags.TAGS[orientation] == 'Orientation':
                        break
                exif = img._getexif()
                if exif:
                    orientation_value = exif.get(orientation)
                    if orientation_value == 3:
                        img = img.rotate(180, expand=True)
                    elif orientation_value == 6:
                        img = img.rotate(270, expand=True)
                    elif orientation_value == 8:
                        img = img.rotate(90, expand=True)
            except (AttributeError, KeyError, IndexError):
                pass
            
            # Convert CMYK to RGB
            if img.mode == 'CMYK':
                img = img.convert('RGB')
                logger.info(f"Converted CMYK to RGB for {filename}")
            
            # Handle transparency
            if target_ext in ['.jpg', '.jpeg']:
                if img.mode in ['RGBA', 'LA', 'PA', 'P']:
                    # Create white background and paste image
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    if img.mode in ['RGBA', 'LA']:
                        background.paste(img, mask=img.split()[-1])
                    else:
                        background.paste(img)
                    img = background
                    logger.info(f"Removed transparency for JPEG conversion: {filename}")
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
            elif target_ext == '.png' and img.mode not in ['RGB', 'RGBA', 'L', 'LA']:
                img = img.convert('RGBA')
            
            # Resize if image is very large (max 4096x4096 for WhatsApp)
            max_dimension = 4096
            if img.width > max_dimension or img.height > max_dimension:
                ratio = min(max_dimension / img.width, max_dimension / img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                logger.info(f"Resized image from {img.size} to {new_size}")
            
            # Save with compression
            output = io.BytesIO()
            
            if target_ext in ['.jpg', '.jpeg']:
                # Start with high quality, reduce if needed
                quality = 95
                img.save(output, format='JPEG', quality=quality, optimize=True)
                
                # Reduce quality if file is too large
                while output.tell() > max_size_bytes and quality > 20:
                    output = io.BytesIO()
                    quality -= 10
                    img.save(output, format='JPEG', quality=quality, optimize=True)
                
                # If still too large, also resize
                if output.tell() > max_size_bytes:
                    scale = 0.9
                    while output.tell() > max_size_bytes and scale > 0.3:
                        new_size = (int(img.width * scale), int(img.height * scale))
                        resized = img.resize(new_size, Image.Resampling.LANCZOS)
                        output = io.BytesIO()
                        resized.save(output, format='JPEG', quality=quality, optimize=True)
                        scale -= 0.1
                
                mime_type = 'image/jpeg'
            else:
                # PNG - try to compress
                img.save(output, format='PNG', optimize=True)
                
                # If PNG is too large, convert to JPEG
                if output.tell() > max_size_bytes:
                    logger.info(f"PNG too large, converting to JPEG: {filename}")
                    if img.mode in ['RGBA', 'LA', 'PA']:
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        background.paste(img, mask=img.split()[-1])
                        img = background
                    elif img.mode != 'RGB':
                        img = img.convert('RGB')
                    
                    output = io.BytesIO()
                    quality = 85
                    img.save(output, format='JPEG', quality=quality, optimize=True)
                    target_ext = '.jpg'
                    mime_type = 'image/jpeg'
                else:
                    mime_type = 'image/png'
            
            output.seek(0)
            new_filename = os.path.splitext(filename)[0] + target_ext
            
            logger.info(f"Image converted: {filename} → {new_filename} ({output.tell()} bytes)")
            
            return output, new_filename, mime_type
            
        except Exception as e:
            raise ConversionError(f"Failed to convert image: {str(e)}")
    
    @classmethod
    def convert_video(cls, file, target_ext: str = '.mp4',
                      max_size_mb: Optional[float] = None,
                      is_template: bool = True) -> Tuple[io.BytesIO, str, str]:
        """
        Convert video to MP4 (H.264) format using FFmpeg.
        
        Features:
        - Convert MOV/AVI/MKV/WebM to MP4
        - Use H.264 codec for compatibility
        - Compress to fit size limits
        - Reduce resolution if needed
        """
        filename = file.name if hasattr(file, 'name') else 'video.mp4'
        
        if max_size_mb is None:
            max_size_mb = 5 if is_template else 16
        
        # Check if FFmpeg is available
        if not cls._check_ffmpeg():
            raise ConversionError(
                "FFmpeg is not installed. Please install FFmpeg to convert videos. "
                "On macOS: brew install ffmpeg. On Linux: apt-get install ffmpeg"
            )
        
        try:
            # Save input file to temp
            file.seek(0)
            with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=False) as input_file:
                input_file.write(file.read())
                input_path = input_file.name
            
            # Create output temp file
            output_path = tempfile.mktemp(suffix='.mp4')
            
            try:
                # Get video duration for bitrate calculation
                duration = cls._get_video_duration(input_path)
                
                # Calculate target bitrate based on max size
                # Formula: bitrate = (max_size_bytes * 8) / duration
                # Leave some room for audio and container overhead
                target_size_bytes = int(max_size_mb * 1024 * 1024 * 0.9)
                
                if duration and duration > 0:
                    target_bitrate = int((target_size_bytes * 8) / duration / 1000)  # kbps
                    target_bitrate = max(target_bitrate, 200)  # Minimum 200kbps
                    target_bitrate = min(target_bitrate, 2000)  # Maximum 2Mbps
                else:
                    target_bitrate = 1000  # Default 1Mbps
                
                # FFmpeg command for conversion
                cmd = [
                    'ffmpeg', '-y',
                    '-i', input_path,
                    '-c:v', 'libx264',  # H.264 codec
                    '-preset', 'medium',
                    '-crf', '23',  # Constant Rate Factor (18-28 is good)
                    '-b:v', f'{target_bitrate}k',
                    '-maxrate', f'{target_bitrate * 2}k',
                    '-bufsize', f'{target_bitrate * 2}k',
                    '-c:a', 'aac',  # AAC audio
                    '-b:a', '128k',
                    '-movflags', '+faststart',  # Enable streaming
                    '-pix_fmt', 'yuv420p',  # Compatibility
                    output_path
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                
                if result.returncode != 0:
                    logger.error(f"FFmpeg error: {result.stderr}")
                    raise ConversionError(f"Video conversion failed: {result.stderr[:200]}")
                
                # Check output size, reduce resolution if still too large
                output_size = os.path.getsize(output_path)
                max_size_bytes = int(max_size_mb * 1024 * 1024)
                
                if output_size > max_size_bytes:
                    # Try with lower resolution
                    os.remove(output_path)
                    output_path = tempfile.mktemp(suffix='.mp4')
                    
                    cmd = [
                        'ffmpeg', '-y',
                        '-i', input_path,
                        '-c:v', 'libx264',
                        '-preset', 'medium',
                        '-crf', '28',  # Higher CRF = lower quality/size
                        '-vf', 'scale=-2:720',  # Scale to 720p max
                        '-c:a', 'aac',
                        '-b:a', '96k',
                        '-movflags', '+faststart',
                        '-pix_fmt', 'yuv420p',
                        output_path
                    ]
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    
                    if result.returncode != 0:
                        raise ConversionError(f"Video conversion failed: {result.stderr[:200]}")
                
                # Read output file
                with open(output_path, 'rb') as f:
                    output = io.BytesIO(f.read())
                output.seek(0)
                
                new_filename = os.path.splitext(filename)[0] + '.mp4'
                logger.info(f"Video converted: {filename} → {new_filename} ({output.tell()} bytes)")
                
                return output, new_filename, 'video/mp4'
                
            finally:
                # Clean up temp files
                if os.path.exists(input_path):
                    os.remove(input_path)
                if os.path.exists(output_path):
                    os.remove(output_path)
                    
        except subprocess.TimeoutExpired:
            raise ConversionError("Video conversion timed out. The file may be too large.")
        except ConversionError:
            raise
        except Exception as e:
            raise ConversionError(f"Failed to convert video: {str(e)}")
    
    @classmethod
    def convert_audio(cls, file, target_ext: str = '.ogg',
                      max_size_mb: Optional[float] = None) -> Tuple[io.BytesIO, str, str]:
        """
        Convert audio to WhatsApp-compatible format using FFmpeg.
        
        Features:
        - Convert WAV/FLAC/M4A to OGG (Opus) or MP3
        - Compress to fit size limits
        - Reduce bitrate if needed
        """
        filename = file.name if hasattr(file, 'name') else 'audio.ogg'
        
        if max_size_mb is None:
            max_size_mb = 16
        
        # Check if FFmpeg is available
        if not cls._check_ffmpeg():
            raise ConversionError(
                "FFmpeg is not installed. Please install FFmpeg to convert audio. "
                "On macOS: brew install ffmpeg. On Linux: apt-get install ffmpeg"
            )
        
        try:
            # Save input file to temp
            file.seek(0)
            with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=False) as input_file:
                input_file.write(file.read())
                input_path = input_file.name
            
            # Create output temp file
            output_path = tempfile.mktemp(suffix=target_ext)
            
            try:
                if target_ext == '.ogg':
                    # Convert to OGG Opus
                    cmd = [
                        'ffmpeg', '-y',
                        '-i', input_path,
                        '-c:a', 'libopus',
                        '-b:a', '64k',  # 64kbps for voice is good
                        '-vbr', 'on',
                        '-compression_level', '10',
                        output_path
                    ]
                    mime_type = 'audio/ogg'
                else:
                    # Convert to MP3
                    cmd = [
                        'ffmpeg', '-y',
                        '-i', input_path,
                        '-c:a', 'libmp3lame',
                        '-b:a', '128k',
                        '-q:a', '2',
                        output_path
                    ]
                    mime_type = 'audio/mpeg'
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                
                if result.returncode != 0:
                    logger.error(f"FFmpeg error: {result.stderr}")
                    raise ConversionError(f"Audio conversion failed: {result.stderr[:200]}")
                
                # Read output file
                with open(output_path, 'rb') as f:
                    output = io.BytesIO(f.read())
                output.seek(0)
                
                new_filename = os.path.splitext(filename)[0] + target_ext
                logger.info(f"Audio converted: {filename} → {new_filename} ({output.tell()} bytes)")
                
                return output, new_filename, mime_type
                
            finally:
                # Clean up temp files
                if os.path.exists(input_path):
                    os.remove(input_path)
                if os.path.exists(output_path):
                    os.remove(output_path)
                    
        except subprocess.TimeoutExpired:
            raise ConversionError("Audio conversion timed out.")
        except ConversionError:
            raise
        except Exception as e:
            raise ConversionError(f"Failed to convert audio: {str(e)}")
    
    @classmethod
    def compress_pdf(cls, file, max_size_mb: float = 100) -> Tuple[io.BytesIO, str, str]:
        """
        Compress PDF using Ghostscript.
        
        Note: Requires Ghostscript to be installed.
        """
        filename = file.name if hasattr(file, 'name') else 'document.pdf'
        max_size_bytes = int(max_size_mb * 1024 * 1024)
        
        file.seek(0)
        original_size = len(file.read())
        file.seek(0)
        
        # If already under limit, return as-is
        if original_size <= max_size_bytes:
            return file, filename, 'application/pdf'
        
        # Check if Ghostscript is available
        if not cls._check_ghostscript():
            logger.warning("Ghostscript not installed. Cannot compress PDF.")
            return file, filename, 'application/pdf'
        
        try:
            # Save input file to temp
            file.seek(0)
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as input_file:
                input_file.write(file.read())
                input_path = input_file.name
            
            output_path = tempfile.mktemp(suffix='.pdf')
            
            try:
                # Ghostscript compression command
                cmd = [
                    'gs', '-sDEVICE=pdfwrite',
                    '-dCompatibilityLevel=1.4',
                    '-dPDFSETTINGS=/ebook',  # Good quality, smaller size
                    '-dNOPAUSE', '-dQUIET', '-dBATCH',
                    f'-sOutputFile={output_path}',
                    input_path
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                
                if result.returncode != 0 or not os.path.exists(output_path):
                    logger.warning(f"PDF compression failed: {result.stderr}")
                    file.seek(0)
                    return file, filename, 'application/pdf'
                
                # Read output file
                with open(output_path, 'rb') as f:
                    output = io.BytesIO(f.read())
                output.seek(0)
                
                # Only use compressed version if it's actually smaller
                if output.tell() < original_size:
                    logger.info(f"PDF compressed: {original_size} → {output.tell()} bytes")
                    return output, filename, 'application/pdf'
                else:
                    file.seek(0)
                    return file, filename, 'application/pdf'
                    
            finally:
                if os.path.exists(input_path):
                    os.remove(input_path)
                if os.path.exists(output_path):
                    os.remove(output_path)
                    
        except Exception as e:
            logger.warning(f"PDF compression failed: {str(e)}")
            file.seek(0)
            return file, filename, 'application/pdf'
    
    @classmethod
    def _check_ffmpeg(cls) -> bool:
        """Check if FFmpeg is installed."""
        try:
            result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False
    
    @classmethod
    def _check_ghostscript(cls) -> bool:
        """Check if Ghostscript is installed."""
        try:
            result = subprocess.run(['gs', '--version'], capture_output=True, timeout=5)
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False
    
    @classmethod
    def _get_video_duration(cls, file_path: str) -> Optional[float]:
        """Get video duration in seconds using FFprobe."""
        try:
            cmd = [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                file_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return float(result.stdout.strip())
        except (subprocess.SubprocessError, ValueError):
            pass
        return None


class AutoMediaConverter:
    """
    Automatic media converter that handles conversion transparently.
    Use in serializers to auto-convert before validation.
    """
    
    @classmethod
    def auto_convert(cls, file, is_template: bool = True) -> Tuple[BinaryIO, str, bool]:
        """
        Automatically convert file if needed.
        
        Returns:
            Tuple of (file, filename, was_converted)
        """
        filename = file.name if hasattr(file, 'name') else 'file'
        needs_conv, media_type, target_ext = MediaConverter.needs_conversion(filename)
        
        if not needs_conv:
            # Check if file needs optimization (size/format issues)
            ext = os.path.splitext(filename.lower())[1]
            
            if ext in ['.jpg', '.jpeg', '.png']:
                try:
                    from PIL import Image
                    file.seek(0)
                    img = Image.open(file)
                    
                    # Check if CMYK
                    if img.mode == 'CMYK':
                        needs_conv = True
                        media_type = 'image'
                    
                    file.seek(0)
                except Exception:
                    pass
            
            if not needs_conv:
                return file, filename, False
        
        try:
            converted_file, new_filename, mime_type = MediaConverter.convert(
                file, 
                target_format=target_ext,
                is_template=is_template
            )
            
            # Create new InMemoryUploadedFile
            from django.core.files.uploadedfile import InMemoryUploadedFile
            
            converted_file.seek(0, 2)  # Seek to end
            size = converted_file.tell()
            converted_file.seek(0)
            
            new_file = InMemoryUploadedFile(
                file=converted_file,
                field_name='media',
                name=new_filename,
                content_type=mime_type,
                size=size,
                charset=None
            )
            
            return new_file, new_filename, True
            
        except ConversionError as e:
            logger.warning(f"Auto-conversion failed: {e}")
            return file, filename, False


def get_conversion_capabilities() -> dict:
    """
    Get available conversion capabilities based on installed tools.
    """
    return {
        'image': {
            'available': True,
            'tool': 'Pillow',
            'formats': list(MediaConverter.IMAGE_CONVERT_MAP.keys()),
            'features': ['CMYK to RGB', 'Transparency removal', 'Resize', 'Compress']
        },
        'video': {
            'available': MediaConverter._check_ffmpeg(),
            'tool': 'FFmpeg',
            'formats': list(MediaConverter.VIDEO_CONVERT_MAP.keys()),
            'features': ['H.264 encoding', 'Resolution scaling', 'Bitrate optimization'],
            'install_hint': 'brew install ffmpeg (macOS) or apt-get install ffmpeg (Linux)'
        },
        'audio': {
            'available': MediaConverter._check_ffmpeg(),
            'tool': 'FFmpeg',
            'formats': list(MediaConverter.AUDIO_CONVERT_MAP.keys()),
            'features': ['Opus encoding', 'MP3 encoding', 'Bitrate optimization'],
            'install_hint': 'brew install ffmpeg (macOS) or apt-get install ffmpeg (Linux)'
        },
        'pdf': {
            'available': MediaConverter._check_ghostscript(),
            'tool': 'Ghostscript',
            'formats': ['.pdf'],
            'features': ['Compression'],
            'install_hint': 'brew install ghostscript (macOS) or apt-get install ghostscript (Linux)'
        }
    }
