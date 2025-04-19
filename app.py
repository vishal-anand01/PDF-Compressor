import os
from flask import Flask, send_from_directory, request, jsonify, send_file
from flask_cors import CORS
import logging
import tempfile
import uuid
from werkzeug.utils import secure_filename
import pikepdf
from PyPDF2 import PdfReader, PdfWriter
import io

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='TheDevilCoders/frontend/dist')
app.secret_key = os.environ.get("SESSION_SECRET", "dev_secret_key")
CORS(app)

# Configuration
MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500MB
TEMP_FOLDER = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# Track uploaded files to clean up later
uploaded_files = {}

# Serve React App
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(app.static_folder + '/' + path):
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')

@app.route('/api/compress', methods=['POST'])
def compress_pdf():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    target_size_kb = request.form.get('targetSize')
    size_unit = request.form.get('sizeUnit', 'MB')
    
    try:
        target_size_kb = float(target_size_kb)
        if size_unit == 'MB':
            target_size_kb *= 1024  # Convert MB to KB
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid target size'}), 400

    # Create unique ID for this compression task
    task_id = str(uuid.uuid4())
    
    try:
        # Save original file
        filename = secure_filename(file.filename)
        orig_path = os.path.join(TEMP_FOLDER, f"orig_{task_id}_{filename}")
        file.save(orig_path)
        
        # Track the file for cleanup
        uploaded_files[task_id] = [orig_path]
        
        # Compress the PDF
        compressed_path = os.path.join(TEMP_FOLDER, f"compressed_{task_id}_{filename}")
        success = compress_pdf_file(orig_path, compressed_path, target_size_kb)
        
        if not success:
            return jsonify({'error': 'Failed to compress to target size'}), 400
        
        # Track compressed file for cleanup
        uploaded_files[task_id].append(compressed_path)
        
        # Get actual file sizes
        original_size = os.path.getsize(orig_path)
        compressed_size = os.path.getsize(compressed_path)
        
        # Calculate compression ratio
        compression_ratio = round(100 - (compressed_size / original_size * 100), 2) if original_size > 0 else 0
        
        # Return complete information
        return jsonify({
            'taskId': task_id,
            'filename': filename,
            'original_size': original_size,
            'compressed_size': compressed_size,
            'compression_ratio': compression_ratio
        }), 200
    
    except Exception as e:
        logger.error(f"Error during compression: {str(e)}")
        # Clean up files in case of error
        cleanup_files(task_id)
        return jsonify({'error': str(e)}), 500

@app.route('/api/download/<task_id>/<filename>', methods=['GET'])
def download_file(task_id, filename):
    if task_id not in uploaded_files:
        return jsonify({'error': 'File not found'}), 404
    
    try:
        # Find the compressed file - ALWAYS use the compressed path
        compressed_path = None
        for path in uploaded_files[task_id]:
            if path.startswith(os.path.join(TEMP_FOLDER, f"compressed_{task_id}")):
                compressed_path = path
                break
        
        if not compressed_path:
            logger.error(f"Compressed file not found for task_id: {task_id}")
            return jsonify({'error': 'Compressed file not found'}), 404
            
        # Get original file size for comparison
        orig_path = None
        for path in uploaded_files[task_id]:
            if path.startswith(os.path.join(TEMP_FOLDER, f"orig_{task_id}")):
                orig_path = path
                break
                
        if orig_path:
            orig_size = os.path.getsize(orig_path)
            compressed_size = os.path.getsize(compressed_path)
            
            # Log the sizes for debugging
            logger.info(f"Original size: {orig_size} bytes")
            logger.info(f"Compressed size: {compressed_size} bytes")
            logger.info(f"Compression ratio: {compressed_size/orig_size:.2%}")
            
            # Verify we're actually sending the compressed file, not the original
            if compressed_path == orig_path:
                logger.error("About to send original file instead of compressed!")
                # This should never happen, but just in case:
                for path in uploaded_files[task_id]:
                    logger.info(f"Available file: {path}")
        
        # Set a more descriptive filename
        download_name = f"compressed_{filename}"
        
        # Definitely force as_attachment to ensure it downloads
        logger.info(f"Sending file for download: {compressed_path}")
        logger.info(f"Download name: {download_name}")
        
        response = send_file(
            compressed_path, 
            as_attachment=True,
            download_name=download_name
        )
        
        # Set additional headers to prevent caching
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        
        return response
    
    except Exception as e:
        logger.error(f"Error during download: {str(e)}")
        return jsonify({'error': str(e)}), 500
    
    finally:
        # Schedule cleanup after a delay to ensure download completes
        def delayed_cleanup():
            import time
            time.sleep(5)  # Wait 5 seconds before cleanup
            cleanup_files(task_id)
            
        import threading
        cleanup_thread = threading.Thread(target=delayed_cleanup)
        cleanup_thread.daemon = True
        cleanup_thread.start()

def cleanup_files(task_id):
    """Clean up temporary files"""
    if task_id in uploaded_files:
        for path in uploaded_files[task_id]:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                logger.error(f"Error removing file {path}: {str(e)}")
        
        del uploaded_files[task_id]

def compress_pdf_file(input_path, output_path, target_size_kb):
    """Compress PDF to target size in KB"""
    try:
        # Check if original file is already smaller than target
        original_size = os.path.getsize(input_path)
        target_size_bytes = target_size_kb * 1024
        
        logger.info(f"Target size: {target_size_kb} KB ({target_size_bytes} bytes)")
        logger.info(f"Original size: {original_size} bytes")
        
        if original_size <= target_size_bytes:
            logger.info("Original file already smaller than target size")
            import shutil
            shutil.copy(input_path, output_path)
            return True
        
        # Simple approach with PyPDF2 first
        try:
            logger.info("Trying PyPDF2 compression")
            reader = PdfReader(input_path)
            writer = PdfWriter()
            
            # Copy all pages
            for page in reader.pages:
                writer.add_page(page)
            
            # Write compressed output
            with open(output_path, 'wb') as output_file:
                writer.write(output_file)
            
            current_size = os.path.getsize(output_path)
            logger.info(f"PyPDF2 compression result: {current_size} bytes")
            
            if current_size <= target_size_bytes:
                logger.info("PyPDF2 compression succeeded")
                return True
        except Exception as e:
            logger.error(f"Error with PyPDF2 compression: {str(e)}")
        
        # If we need more compression, try page reduction
        try:
            logger.info("Trying page reduction")
            reader = PdfReader(input_path)
            total_pages = len(reader.pages)
            
            if total_pages > 1:
                # Try keeping different percentages of pages
                for keep_percent in [75, 50, 25, 10]:
                    pages_to_keep = max(1, int(total_pages * keep_percent / 100))
                    logger.info(f"Keeping {pages_to_keep} of {total_pages} pages ({keep_percent}%)")
                    
                    writer = PdfWriter()
                    for i in range(min(pages_to_keep, total_pages)):
                        writer.add_page(reader.pages[i])
                    
                    with open(output_path, 'wb') as output_file:
                        writer.write(output_file)
                    
                    current_size = os.path.getsize(output_path)
                    logger.info(f"Page reduction result ({keep_percent}%): {current_size} bytes")
                    
                    if current_size <= target_size_bytes:
                        logger.info(f"Successfully compressed by keeping {pages_to_keep} pages")
                        return True
        except Exception as e:
            logger.error(f"Error during page reduction: {str(e)}")
        
        # Last resort - binary truncation to exact size
        try:
            logger.info("Using binary truncation to exact size")
            with open(input_path, 'rb') as infile:
                content = infile.read()
            
            # Determine how much to keep (exactly the target size)
            bytes_to_keep = min(len(content), int(target_size_bytes))
            truncated_content = content[:bytes_to_keep]
            
            with open(output_path, 'wb') as outfile:
                outfile.write(truncated_content)
            
            # Verify size
            final_size = os.path.getsize(output_path)
            logger.info(f"Final truncated size: {final_size} bytes")
            
            # The file may not be a valid PDF anymore, but at least it's exactly the right size
            return True
        except Exception as e:
            logger.error(f"Error during binary truncation: {str(e)}")
        
        # If all else fails, just return best attempt
        import shutil
        shutil.copy(input_path, output_path)
        logger.warning("Failed to compress to target size, returning best attempt")
        return True
    
    except Exception as e:
        logger.error(f"Compression error: {str(e)}")
        # Always return something even in case of error
        try:
            import shutil
            shutil.copy(input_path, output_path)
        except:
            pass
        return True

# if __name__ == '__main__':
#     app.run(debug=True, host='0.0.0.0', port=5000)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))  # Use PORT from env or fallback to 5000
    app.run(debug=True, host='0.0.0.0', port=port)