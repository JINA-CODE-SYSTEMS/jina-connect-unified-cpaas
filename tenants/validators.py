"""
Media validators for WhatsApp template uploads.
Based on Meta/Gupshup requirements for different media types.
"""
import os
from django.core.exceptions import ValidationError
from rest_framework import serializers

# Try to import magic, but make it optional
try:
    import magic
    MAGIC_AVAILABLE = True
except ImportError:
    MAGIC_AVAILABLE = False
    import warnings
    warnings.warn(
        "python-magic is not available. MIME type detection will be limited. "
        "Install libmagic: 'brew install libmagic' (macOS) or 'apt-get install libmagic1' (Linux)",
        ImportWarning
    )


class MediaTypeConfig:
    """Configuration for each media type with validation rules."""
    
    # Document configurations
    DOCUMENT = {
        'extensions': ['.pdf', '.docx', '.xlsx', '.pptx'],
        'mime_types': [
            'application/pdf',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        ],
        'max_size_mb': 100,
        'max_size_bytes': 100 * 1024 * 1024,  # 100 MB
        'description': 'Document (PDF, DOCX, XLSX, PPTX)',
    }
    
    # Image configurations
    IMAGE = {
        'extensions': ['.jpg', '.jpeg', '.png'],
        'mime_types': [
            'image/jpeg',
            'image/png',
        ],
        'max_size_mb_template': 5,
        'max_size_mb_message': 16,
        'max_size_bytes_template': 5 * 1024 * 1024,  # 5 MB for templates
        'max_size_bytes_message': 16 * 1024 * 1024,  # 16 MB for messages
        'description': 'Image (JPG, JPEG, PNG)',
    }
    
    # Video configurations
    VIDEO = {
        'extensions': ['.mp4'],
        'mime_types': [
            'video/mp4',
        ],
        'max_size_mb_template': 16,
        'max_size_mb_message': 16,
        'max_size_bytes_template': 16 * 1024 * 1024,  # 16 MB for templates (Meta allows 16 MB)
        'max_size_bytes_message': 16 * 1024 * 1024,  # 16 MB for messages
        'description': 'Video (MP4 H.264)',
    }
    
    # Audio configurations
    AUDIO = {
        'extensions': ['.ogg', '.amr', '.mp3'],
        'mime_types': [
            'audio/ogg',
            'audio/amr',
            'audio/mpeg',
            'audio/mp3',
        ],
        'max_size_mb': 16,
        'max_size_bytes': 16 * 1024 * 1024,  # 16 MB
        'description': 'Audio (OGG, AMR, MP3)',
    }


class MediaValidationError(Exception):
    """Custom exception for media validation errors."""
    pass


class WhatsAppMediaValidator:
    """
    Comprehensive media validator for WhatsApp template uploads.
    Validates file type, MIME type, size, and content integrity.
    """
    
    # Disallowed content patterns
    BLOCKED_DOCUMENT_PATTERNS = [
        'password-protected',
        'encrypted',
    ]
    
    # All supported extensions
    ALL_SUPPORTED_EXTENSIONS = (
        MediaTypeConfig.DOCUMENT['extensions'] +
        MediaTypeConfig.IMAGE['extensions'] +
        MediaTypeConfig.VIDEO['extensions'] +
        MediaTypeConfig.AUDIO['extensions']
    )
    
    # All supported MIME types
    ALL_SUPPORTED_MIME_TYPES = (
        MediaTypeConfig.DOCUMENT['mime_types'] +
        MediaTypeConfig.IMAGE['mime_types'] +
        MediaTypeConfig.VIDEO['mime_types'] +
        MediaTypeConfig.AUDIO['mime_types']
    )
    
    @classmethod
    def get_media_type(cls, file):
        """
        Determine the media type category based on file extension.
        Returns: 'document', 'image', 'video', 'audio', or None
        """
        filename = file.name.lower() if hasattr(file, 'name') else str(file).lower()
        ext = os.path.splitext(filename)[1].lower()
        
        if ext in MediaTypeConfig.DOCUMENT['extensions']:
            return 'document'
        elif ext in MediaTypeConfig.IMAGE['extensions']:
            return 'image'
        elif ext in MediaTypeConfig.VIDEO['extensions']:
            return 'video'
        elif ext in MediaTypeConfig.AUDIO['extensions']:
            return 'audio'
        return None
    
    @classmethod
    def get_file_extension(cls, file):
        """Get the file extension from the uploaded file."""
        filename = file.name.lower() if hasattr(file, 'name') else str(file).lower()
        return os.path.splitext(filename)[1].lower()
    
    @classmethod
    def get_mime_type(cls, file):
        """
        Detect MIME type using python-magic for accurate detection.
        Falls back to file extension-based detection if magic fails or is not available.
        """
        # Try python-magic if available
        if MAGIC_AVAILABLE:
            try:
                # Reset file pointer to beginning
                if hasattr(file, 'seek'):
                    file.seek(0)
                
                # Read first 2048 bytes for magic number detection
                if hasattr(file, 'read'):
                    header = file.read(2048)
                    file.seek(0)  # Reset after reading
                    
                    mime = magic.Magic(mime=True)
                    detected_mime = mime.from_buffer(header)
                    return detected_mime
            except Exception:
                pass
        
        # Fallback to content_type if available
        if hasattr(file, 'content_type') and file.content_type:
            return file.content_type
        
        # Fallback to extension-based detection
        ext = cls.get_file_extension(file)
        mime_map = {
            '.pdf': 'application/pdf',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.mp4': 'video/mp4',
            '.ogg': 'audio/ogg',
            '.mp3': 'audio/mpeg',
            '.amr': 'audio/amr',
        }
        return mime_map.get(ext)
    
    @classmethod
    def validate_extension(cls, file):
        """
        Validate that the file has an allowed extension.
        """
        ext = cls.get_file_extension(file)
        
        if ext not in cls.ALL_SUPPORTED_EXTENSIONS:
            raise MediaValidationError(
                f"Unsupported file extension '{ext}'. "
                f"Allowed extensions: {', '.join(cls.ALL_SUPPORTED_EXTENSIONS)}"
            )
        
        return ext
    
    @classmethod
    def validate_mime_type(cls, file, expected_category=None):
        """
        Validate MIME type matches the file extension.
        Prevents file extension spoofing (e.g., renaming .exe to .pdf).
        Note: If python-magic is not available, validation will be more lenient.
        """
        detected_mime = cls.get_mime_type(file)
        ext = cls.get_file_extension(file)
        media_type = cls.get_media_type(file)
        
        if not detected_mime:
            # If magic is not available, we can't do deep validation
            if not MAGIC_AVAILABLE:
                # Just warn and continue
                import warnings
                warnings.warn(
                    f"MIME type validation skipped for {file.name if hasattr(file, 'name') else 'file'}. "
                    "Install libmagic for better security.",
                    RuntimeWarning
                )
                return None
            else:
                raise MediaValidationError(
                    "Could not detect file MIME type. The file may be corrupted or invalid."
                )
        
        # Get expected MIME types for this media category
        if media_type == 'document':
            expected_mimes = MediaTypeConfig.DOCUMENT['mime_types']
        elif media_type == 'image':
            expected_mimes = MediaTypeConfig.IMAGE['mime_types']
        elif media_type == 'video':
            expected_mimes = MediaTypeConfig.VIDEO['mime_types']
        elif media_type == 'audio':
            expected_mimes = MediaTypeConfig.AUDIO['mime_types']
        else:
            expected_mimes = cls.ALL_SUPPORTED_MIME_TYPES
        
        if detected_mime not in expected_mimes:
            raise MediaValidationError(
                f"File content does not match extension '{ext}'. "
                f"Detected type: '{detected_mime}'. "
                f"This may indicate a renamed or corrupted file."
            )
        
        return detected_mime
    
    @classmethod
    def validate_file_size(cls, file, is_template=True):
        """
        Validate file size based on media type and usage context.
        
        Args:
            file: The uploaded file
            is_template: If True, use stricter template limits (5MB for images/videos)
                        If False, use message limits (16MB)
        """
        file_size = file.size if hasattr(file, 'size') else 0
        media_type = cls.get_media_type(file)
        
        if media_type == 'document':
            max_size = MediaTypeConfig.DOCUMENT['max_size_bytes']
            max_size_mb = MediaTypeConfig.DOCUMENT['max_size_mb']
        elif media_type == 'image':
            max_size = (MediaTypeConfig.IMAGE['max_size_bytes_template'] 
                       if is_template else MediaTypeConfig.IMAGE['max_size_bytes_message'])
            max_size_mb = (MediaTypeConfig.IMAGE['max_size_mb_template'] 
                          if is_template else MediaTypeConfig.IMAGE['max_size_mb_message'])
        elif media_type == 'video':
            max_size = (MediaTypeConfig.VIDEO['max_size_bytes_template'] 
                       if is_template else MediaTypeConfig.VIDEO['max_size_bytes_message'])
            max_size_mb = (MediaTypeConfig.VIDEO['max_size_mb_template'] 
                          if is_template else MediaTypeConfig.VIDEO['max_size_mb_message'])
        elif media_type == 'audio':
            max_size = MediaTypeConfig.AUDIO['max_size_bytes']
            max_size_mb = MediaTypeConfig.AUDIO['max_size_mb']
        else:
            # Default to most restrictive limit
            max_size = 5 * 1024 * 1024
            max_size_mb = 5
        
        if file_size > max_size:
            current_size_mb = round(file_size / (1024 * 1024), 2)
            raise MediaValidationError(
                f"File size ({current_size_mb} MB) exceeds maximum allowed "
                f"({max_size_mb} MB) for {media_type or 'this file type'}."
            )
        
        return file_size
    
    @classmethod
    def validate_document(cls, file):
        """
        Comprehensive validation for documents (PDF, DOCX, XLSX, PPTX).
        
        Based on Meta/WhatsApp requirements:
        - PDF: Most stable & recommended
        - DOCX: Word documents
        - XLSX: Excel spreadsheets
        - PPTX: PowerPoint presentations
        
        PDF-specific checks:
        - Valid PDF header
        - PDF version <= 1.7
        - No encryption/password protection
        - No embedded multimedia
        - No JavaScript
        - Detects renamed images masquerading as PDFs
        """
        ext = cls.get_file_extension(file)
        
        if ext == '.pdf':
            cls._validate_pdf(file)
        elif ext == '.docx':
            cls._validate_docx(file)
        elif ext == '.xlsx':
            cls._validate_xlsx(file)
        elif ext == '.pptx':
            cls._validate_pptx(file)
        
        return True
    
    @classmethod
    def _validate_pdf(cls, file):
        """
        Validate PDF files according to WhatsApp/Meta requirements.
        
        ✅ DOs:
        - Use PDF (most stable & recommended by Meta)
        - Keep file simple & flat (NO layers, NO embedded fonts)
        - Single-page PDF recommended
        - Export using: Save as → PDF → Minimal / Small File Size
        - Max size: 100 MB
        - MIME type = application/pdf
        
        ❌ DON'Ts:
        - Don't upload PowerPoint-style PDFs with layers, transparency, or slide elements
        - Don't use password-protected PDFs
        - Don't use scanned PDFs with OCR issues
        - Don't embed audio, video, hyperlinks
        - Don't use PDF version > 1.7
        - Don't rename images to .pdf
        """
        try:
            if hasattr(file, 'seek'):
                file.seek(0)
            
            # Read enough bytes for thorough inspection
            # First 8KB for header analysis, then check for embedded content
            header = file.read(8192)
            file.seek(0)
            
            # Read entire file for deep inspection (up to 10MB for scanning)
            file_size = file.size if hasattr(file, 'size') else len(header)
            if file_size < 10 * 1024 * 1024:  # Only deep scan files < 10MB
                full_content = file.read()
                file.seek(0)
            else:
                full_content = header  # For large files, just check header
            
            # ========== Check 1: Valid PDF header ==========
            if not header.startswith(b'%PDF-'):
                # Check if it's actually an image renamed to PDF
                if cls._is_image_content(header):
                    raise MediaValidationError(
                        "This file appears to be an image renamed to .pdf. "
                        "Please upload the original image file, or properly convert it to PDF."
                    )
                raise MediaValidationError(
                    "Invalid PDF file. The file does not have a valid PDF header. "
                    "Please ensure you're uploading a valid PDF document."
                )
            
            # ========== Check 2: PDF Version ==========
            header_str = header.decode('latin-1', errors='ignore')
            if '%PDF-' in header_str:
                version_start = header_str.index('%PDF-') + 5
                version_str = header_str[version_start:version_start + 3]
                try:
                    version = float(version_str)
                    if version > 1.7:
                        raise MediaValidationError(
                            f"PDF version {version} is not supported by WhatsApp. "
                            f"Please use PDF version 1.7 or lower. "
                            f"Tip: Re-export the PDF using 'Save as PDF' with compatibility mode."
                        )
                except ValueError:
                    pass  # Could not parse version, continue
            
            # ========== Check 3: Encryption/Password Protection ==========
            if b'/Encrypt' in full_content:
                raise MediaValidationError(
                    "Password-protected or encrypted PDFs are not supported by WhatsApp. "
                    "Please provide an unencrypted PDF document."
                )
            
            # ========== Check 4: Embedded JavaScript ==========
            js_patterns = [b'/JavaScript', b'/JS ', b'/JS(', b'/JS<']
            for pattern in js_patterns:
                if pattern in full_content:
                    raise MediaValidationError(
                        "PDFs with embedded JavaScript are not supported. "
                        "Please remove any JavaScript/interactive elements from the PDF."
                    )
            
            # ========== Check 5: Embedded Multimedia ==========
            multimedia_patterns = [
                (b'/RichMedia', "embedded rich media"),
                (b'/Movie', "embedded video/movie"),
                (b'/Sound', "embedded audio/sound"),
                (b'/Screen', "embedded screen annotations"),
                (b'/3D', "embedded 3D content"),
            ]
            for pattern, description in multimedia_patterns:
                if pattern in full_content:
                    raise MediaValidationError(
                        f"PDFs with {description} are not supported by WhatsApp. "
                        f"Please use a simple, flat PDF without multimedia content."
                    )
            
            # ========== Check 6: Embedded Files/Attachments ==========
            if b'/EmbeddedFiles' in full_content or b'/EmbeddedFile' in full_content:
                raise MediaValidationError(
                    "PDFs with embedded files/attachments are not supported. "
                    "Please remove any attachments from the PDF."
                )
            
            # ========== Check 7: Forms/AcroForms ==========
            if b'/AcroForm' in full_content:
                raise MediaValidationError(
                    "PDFs with interactive forms (AcroForms) are not recommended. "
                    "Please flatten the form or use a static PDF."
                )
            
            # ========== Check 8: External Links/Actions (Warning) ==========
            # Note: We don't block these, but they won't work in WhatsApp
            has_links = b'/URI' in full_content or b'/GoTo' in full_content
            
            # ========== Check 9: Detect problematic scanned PDFs ==========
            # Scanned PDFs typically have very large images and minimal text
            # This is a heuristic check
            image_count = full_content.count(b'/Image') + full_content.count(b'/XObject')
            text_markers = full_content.count(b'/Font') + full_content.count(b'Tj') + full_content.count(b'TJ')
            
            # If there are many images but very few text markers, might be a scan
            if image_count > 5 and text_markers < 3:
                # This is just a warning-level check, don't block
                pass
                
        except MediaValidationError:
            raise
        except Exception as e:
            # Log but don't fail on PDF inspection errors for edge cases
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"PDF inspection encountered an error: {str(e)}")
    
    @classmethod
    def _is_image_content(cls, header):
        """
        Detect if file content is actually an image (JPEG, PNG, GIF, etc.)
        Used to catch renamed image files.
        """
        # JPEG magic bytes
        if header.startswith(b'\xff\xd8\xff'):
            return True
        
        # PNG magic bytes
        if header.startswith(b'\x89PNG\r\n\x1a\n'):
            return True
        
        # GIF magic bytes
        if header.startswith(b'GIF87a') or header.startswith(b'GIF89a'):
            return True
        
        # WebP magic bytes
        if header.startswith(b'RIFF') and b'WEBP' in header[:12]:
            return True
        
        # BMP magic bytes
        if header.startswith(b'BM'):
            return True
        
        # TIFF magic bytes (little-endian and big-endian)
        if header.startswith(b'II\x2a\x00') or header.startswith(b'MM\x00\x2a'):
            return True
        
        return False
    
    @classmethod
    def _validate_docx(cls, file):
        """
        Validate DOCX (Word) files.
        DOCX files are ZIP archives with specific structure.
        """
        try:
            if hasattr(file, 'seek'):
                file.seek(0)
            
            header = file.read(4)
            file.seek(0)
            
            # DOCX files are ZIP archives, check for ZIP magic number
            if header != b'PK\x03\x04':
                raise MediaValidationError(
                    "Invalid DOCX file. The file does not appear to be a valid Word document. "
                    "Please ensure you're uploading a .docx file (not the older .doc format)."
                )
            
            # Try to verify it's actually a DOCX by checking for required components
            try:
                import zipfile
                import io
                
                if hasattr(file, 'seek'):
                    file.seek(0)
                
                # Create a bytes buffer for zipfile
                if hasattr(file, 'read'):
                    content = file.read()
                    file.seek(0)
                    zip_buffer = io.BytesIO(content)
                else:
                    zip_buffer = file
                
                with zipfile.ZipFile(zip_buffer, 'r') as zf:
                    namelist = zf.namelist()
                    
                    # DOCX must have [Content_Types].xml
                    if '[Content_Types].xml' not in namelist:
                        raise MediaValidationError(
                            "Invalid DOCX file structure. The file may be corrupted or "
                            "is not a valid Word document."
                        )
                    
                    # Check for DOCX-specific directory
                    has_word_dir = any(name.startswith('word/') for name in namelist)
                    if not has_word_dir:
                        raise MediaValidationError(
                            "Invalid DOCX file. Missing required Word document structure."
                        )
                    
                    # Check for macros (potential security risk)
                    if 'word/vbaProject.bin' in namelist:
                        raise MediaValidationError(
                            "DOCX files with macros (VBA) are not supported. "
                            "Please remove macros or save as a regular .docx file."
                        )
                        
            except zipfile.BadZipFile:
                raise MediaValidationError(
                    "Invalid or corrupted DOCX file. Please re-export the document."
                )
            except ImportError:
                # zipfile should always be available, but just in case
                pass
                
        except MediaValidationError:
            raise
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"DOCX inspection encountered an error: {str(e)}")
    
    @classmethod
    def _validate_xlsx(cls, file):
        """
        Validate XLSX (Excel) files.
        XLSX files are ZIP archives with specific structure.
        """
        try:
            if hasattr(file, 'seek'):
                file.seek(0)
            
            header = file.read(4)
            file.seek(0)
            
            # XLSX files are ZIP archives
            if header != b'PK\x03\x04':
                raise MediaValidationError(
                    "Invalid XLSX file. The file does not appear to be a valid Excel document. "
                    "Please ensure you're uploading a .xlsx file (not the older .xls format)."
                )
            
            try:
                import zipfile
                import io
                
                if hasattr(file, 'seek'):
                    file.seek(0)
                
                if hasattr(file, 'read'):
                    content = file.read()
                    file.seek(0)
                    zip_buffer = io.BytesIO(content)
                else:
                    zip_buffer = file
                
                with zipfile.ZipFile(zip_buffer, 'r') as zf:
                    namelist = zf.namelist()
                    
                    # XLSX must have [Content_Types].xml
                    if '[Content_Types].xml' not in namelist:
                        raise MediaValidationError(
                            "Invalid XLSX file structure. The file may be corrupted or "
                            "is not a valid Excel document."
                        )
                    
                    # Check for XLSX-specific directory
                    has_xl_dir = any(name.startswith('xl/') for name in namelist)
                    if not has_xl_dir:
                        raise MediaValidationError(
                            "Invalid XLSX file. Missing required Excel document structure."
                        )
                    
                    # Check for macros
                    if 'xl/vbaProject.bin' in namelist:
                        raise MediaValidationError(
                            "XLSX files with macros (VBA) are not supported. "
                            "Please remove macros or save as a regular .xlsx file."
                        )
                        
            except zipfile.BadZipFile:
                raise MediaValidationError(
                    "Invalid or corrupted XLSX file. Please re-export the document."
                )
            except ImportError:
                pass
                
        except MediaValidationError:
            raise
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"XLSX inspection encountered an error: {str(e)}")
    
    @classmethod
    def _validate_pptx(cls, file):
        """
        Validate PPTX (PowerPoint) files.
        PPTX files are ZIP archives with specific structure.
        
        Note: PowerPoint files converted to PDF should be simple/flat.
        Complex slide elements may cause issues.
        """
        try:
            if hasattr(file, 'seek'):
                file.seek(0)
            
            header = file.read(4)
            file.seek(0)
            
            # PPTX files are ZIP archives
            if header != b'PK\x03\x04':
                raise MediaValidationError(
                    "Invalid PPTX file. The file does not appear to be a valid PowerPoint document. "
                    "Please ensure you're uploading a .pptx file (not the older .ppt format)."
                )
            
            try:
                import zipfile
                import io
                
                if hasattr(file, 'seek'):
                    file.seek(0)
                
                if hasattr(file, 'read'):
                    content = file.read()
                    file.seek(0)
                    zip_buffer = io.BytesIO(content)
                else:
                    zip_buffer = file
                
                with zipfile.ZipFile(zip_buffer, 'r') as zf:
                    namelist = zf.namelist()
                    
                    # PPTX must have [Content_Types].xml
                    if '[Content_Types].xml' not in namelist:
                        raise MediaValidationError(
                            "Invalid PPTX file structure. The file may be corrupted or "
                            "is not a valid PowerPoint document."
                        )
                    
                    # Check for PPTX-specific directory
                    has_ppt_dir = any(name.startswith('ppt/') for name in namelist)
                    if not has_ppt_dir:
                        raise MediaValidationError(
                            "Invalid PPTX file. Missing required PowerPoint document structure."
                        )
                    
                    # Check for macros
                    if 'ppt/vbaProject.bin' in namelist:
                        raise MediaValidationError(
                            "PPTX files with macros (VBA) are not supported. "
                            "Please remove macros or save as a regular .pptx file."
                        )
                    
                    # Check for embedded media (video/audio) - these may cause issues
                    media_files = [n for n in namelist if n.startswith('ppt/media/')]
                    video_extensions = ['.mp4', '.avi', '.mov', '.wmv', '.mp3', '.wav']
                    has_embedded_media = any(
                        any(n.lower().endswith(ext) for ext in video_extensions)
                        for n in media_files
                    )
                    if has_embedded_media:
                        raise MediaValidationError(
                            "PPTX files with embedded video or audio are not recommended. "
                            "Please remove multimedia content or export as a simple PDF."
                        )
                        
            except zipfile.BadZipFile:
                raise MediaValidationError(
                    "Invalid or corrupted PPTX file. Please re-export the document."
                )
            except ImportError:
                pass
                
        except MediaValidationError:
            raise
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"PPTX inspection encountered an error: {str(e)}")
    
    @classmethod
    def validate_image(cls, file):
        """
        Additional validation specific to images.
        - Checks for CMYK color format (not recommended)
        - Validates image integrity
        """
        try:
            from PIL import Image
            
            if hasattr(file, 'seek'):
                file.seek(0)
            
            img = Image.open(file)
            
            # Check for CMYK color mode (not recommended for WhatsApp)
            if img.mode == 'CMYK':
                raise MediaValidationError(
                    "CMYK color format is not supported. "
                    "Please convert the image to RGB color format."
                )
            
            # Check for transparency in JPEG (not supported)
            ext = cls.get_file_extension(file)
            if ext in ['.jpg', '.jpeg'] and img.mode in ['RGBA', 'LA', 'PA']:
                raise MediaValidationError(
                    "JPEG images with transparency are not supported. "
                    "Please use PNG for transparent images or remove transparency."
                )
            
            # Reset file pointer
            if hasattr(file, 'seek'):
                file.seek(0)
                
        except MediaValidationError:
            raise
        except ImportError:
            # PIL not available, skip advanced image validation
            pass
        except Exception as e:
            raise MediaValidationError(
                f"Invalid or corrupted image file: {str(e)}"
            )
        
        return True
    
    @classmethod
    def validate_video(cls, file):
        """
        Additional validation specific to videos.
        - Checks for supported codec (H.264)
        """
        ext = cls.get_file_extension(file)
        
        if ext not in ['.mp4']:
            raise MediaValidationError(
                f"Video format '{ext}' is not supported. "
                f"Please use MP4 format with H.264 codec."
            )
        
        # Basic MP4 header check
        try:
            if hasattr(file, 'seek'):
                file.seek(0)
            
            header = file.read(12)
            file.seek(0)
            
            # Check for ftyp box (MP4 signature)
            if b'ftyp' not in header:
                raise MediaValidationError(
                    "Invalid MP4 file. The file does not have a valid MP4 header."
                )
                
        except MediaValidationError:
            raise
        except Exception:
            pass
        
        return True
    
    @classmethod
    def validate_audio(cls, file):
        """
        Additional validation specific to audio files.
        """
        ext = cls.get_file_extension(file)
        
        # Recommend OGG (Opus) as best supported
        if ext not in MediaTypeConfig.AUDIO['extensions']:
            raise MediaValidationError(
                f"Audio format '{ext}' is not supported. "
                f"Recommended: OGG (Opus). Also supported: AMR, MP3."
            )
        
        return True
    
    @classmethod
    def validate(cls, file, is_template=True):
        """
        Full validation pipeline for media files.
        
        Args:
            file: The uploaded file object
            is_template: If True, apply stricter template limits
        
        Returns:
            dict: Validation result with file info
        
        Raises:
            MediaValidationError: If any validation fails
        """
        errors = []
        
        # Step 1: Validate extension
        try:
            ext = cls.validate_extension(file)
        except MediaValidationError as e:
            errors.append(str(e))
            raise MediaValidationError(errors[0])
        
        # Step 2: Validate MIME type
        try:
            mime_type = cls.validate_mime_type(file)
        except MediaValidationError as e:
            errors.append(str(e))
            raise MediaValidationError(errors[0])
        
        # Step 3: Validate file size
        try:
            file_size = cls.validate_file_size(file, is_template=is_template)
        except MediaValidationError as e:
            errors.append(str(e))
            raise MediaValidationError(errors[0])
        
        # Step 4: Type-specific validation
        media_type = cls.get_media_type(file)
        
        try:
            if media_type == 'document':
                cls.validate_document(file)
            elif media_type == 'image':
                cls.validate_image(file)
            elif media_type == 'video':
                cls.validate_video(file)
            elif media_type == 'audio':
                cls.validate_audio(file)
        except MediaValidationError as e:
            errors.append(str(e))
            raise MediaValidationError(errors[0])
        
        return {
            'valid': True,
            'extension': ext,
            'mime_type': mime_type,
            'file_size': file_size,
            'media_type': media_type,
        }


def validate_whatsapp_media(file, is_template=True):
    """
    Django validator function for WhatsApp media files.
    Can be used in model FileField validators.
    
    Usage:
        media = models.FileField(upload_to='media/', validators=[validate_whatsapp_media])
    """
    try:
        WhatsAppMediaValidator.validate(file, is_template=is_template)
    except MediaValidationError as e:
        raise ValidationError(str(e))


def validate_template_media(file):
    """Validator specifically for template media (stricter 5MB limit for images/videos)."""
    return validate_whatsapp_media(file, is_template=True)


def validate_message_media(file):
    """Validator for message media (16MB limit for images/videos)."""
    return validate_whatsapp_media(file, is_template=False)


class WhatsAppMediaSerializerValidator:
    """
    Serializer field validator for WhatsApp media.
    Use in DRF serializers.
    
    Usage:
        media = serializers.FileField(validators=[WhatsAppMediaSerializerValidator()])
    """
    
    def __init__(self, is_template=True):
        self.is_template = is_template
    
    def __call__(self, value):
        try:
            result = WhatsAppMediaValidator.validate(value, is_template=self.is_template)
            return value
        except MediaValidationError as e:
            raise serializers.ValidationError(str(e))


# Convenience functions for getting validation rules as documentation
def get_document_rules():
    """Get validation rules for documents (PDF, DOCX, XLSX, PPTX)."""
    return {
        'allowed_extensions': MediaTypeConfig.DOCUMENT['extensions'],
        'max_size_mb': MediaTypeConfig.DOCUMENT['max_size_mb'],
        'recommendations': [
            'Use PDF (most stable & recommended by Meta)',
            'Keep file simple & flat (NO layers, NO embedded fonts)',
            'Single-page PDF recommended',
            'Export using: Save as → PDF → Minimal / Small File Size',
            'Ensure MIME type = application/pdf for PDFs',
            'For DOCX/XLSX/PPTX: Use modern Office Open XML formats (.docx, .xlsx, .pptx)',
        ],
        'restrictions': [
            'No password-protected or encrypted documents',
            'No PDFs with embedded JavaScript',
            'No embedded audio, video, or multimedia content',
            'No embedded files or attachments',
            'No interactive forms (AcroForms)',
            'PDF version must be 1.7 or lower',
            'No scanned PDFs with OCR issues',
            'Do not rename images to .pdf',
            'No macros (VBA) in Office documents',
            'No old format files (.doc, .xls, .ppt) - use modern formats',
        ],
        'pdf_specific': {
            'version': '1.7 or lower',
            'encryption': 'Not allowed',
            'javascript': 'Not allowed',
            'multimedia': 'Not allowed (audio, video, 3D, rich media)',
            'forms': 'Not recommended (AcroForms)',
            'hyperlinks': 'Won\'t work in WhatsApp (but allowed)',
        },
        'office_specific': {
            'macros': 'Not allowed (vbaProject.bin)',
            'embedded_media': 'Not recommended in PPTX',
            'format': 'Must be Office Open XML (.docx, .xlsx, .pptx)',
        },
    }


def get_image_rules(is_template=True):
    """Get validation rules for images."""
    return {
        'allowed_extensions': MediaTypeConfig.IMAGE['extensions'],
        'max_size_mb': (MediaTypeConfig.IMAGE['max_size_mb_template'] 
                       if is_template else MediaTypeConfig.IMAGE['max_size_mb_message']),
        'recommendations': [
            'Resolution: 1080×1080 recommended',
            'Use RGB colors',
            'Use compressed, clean images',
        ],
        'restrictions': [
            'No transparency in JPEG',
            'No layered PSD exports',
            'No CMYK color format',
            'No blurry/distorted/low quality visuals',
            'No copyrighted content (logos are fine)',
        ],
    }


def get_video_rules(is_template=True):
    """Get validation rules for videos."""
    return {
        'allowed_extensions': MediaTypeConfig.VIDEO['extensions'],
        'max_size_mb': (MediaTypeConfig.VIDEO['max_size_mb_template'] 
                       if is_template else MediaTypeConfig.VIDEO['max_size_mb_message']),
        'recommendations': [
            'Use MP4 with H.264 codec',
            'Use low-to-medium bitrate',
        ],
        'restrictions': [
            'No MOV or AVI formats',
            'No audio track issues',
            'No variable frame rate',
            'No very high resolution (avoid HD 1080p for templates)',
        ],
    }


def get_audio_rules():
    """Get validation rules for audio."""
    return {
        'allowed_extensions': MediaTypeConfig.AUDIO['extensions'],
        'max_size_mb': MediaTypeConfig.AUDIO['max_size_mb'],
        'recommendations': [
            'Use OGG (Opus) — best supported',
            'Bitrate < 96 kbps recommended',
        ],
        'restrictions': [
            'No WAV or FLAC formats',
            'No multi-channel audio',
        ],
    }
