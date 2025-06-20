from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
import os
import zipfile
import tempfile
import requests
from pdf2image import convert_from_path
import shutil
import logging
from typing import Optional
import uuid

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="PDF2JPG Service", version="2.0.0")

# Configuración
TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)

def download_pdf_from_url(url: str, output_path: str) -> bool:
    """Descarga un PDF desde una URL"""
    try:
        logger.info(f"Descargando PDF desde: {url}")
        
        # Headers para Google Drive y otros servicios
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/pdf,*/*'
        }
        
        # Hacer request con streaming
        response = requests.get(url, headers=headers, stream=True, timeout=60)
        response.raise_for_status()
        
        # Verificar que es un PDF
        content_type = response.headers.get('content-type', '').lower()
        if 'pdf' not in content_type and not url.endswith('.pdf'):
            logger.warning(f"Content-Type sospechoso: {content_type}")
        
        # Guardar archivo
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        # Verificar que se descargó algo
        if os.path.getsize(output_path) == 0:
            logger.error("Archivo descargado está vacío")
            return False
            
        logger.info(f"PDF descargado exitosamente: {os.path.getsize(output_path)} bytes")
        return True
        
    except Exception as e:
        logger.error(f"Error descargando PDF: {str(e)}")
        if os.path.exists(output_path):
            os.remove(output_path)
        return False

def convert_pdf_to_images(pdf_path: str, output_dir: str) -> list:
    """Convierte PDF a imágenes JPG"""
    try:
        logger.info(f"Convirtiendo PDF: {pdf_path}")
        
        # Convertir PDF a imágenes
        images = convert_from_path(
            pdf_path,
            dpi=200,  # DPI optimizado para calidad/velocidad
            output_folder=output_dir,
            fmt='jpeg',
            thread_count=2  # Limitado para Railway
        )
        
        image_paths = []
        for i, image in enumerate(images):
            image_path = os.path.join(output_dir, f'page_{i+1:03d}.jpg')
            image.save(image_path, 'JPEG', quality=85, optimize=True)
            image_paths.append(image_path)
            logger.info(f"Guardada página {i+1}: {image_path}")
        
        logger.info(f"Conversión completada: {len(image_paths)} imágenes")
        return image_paths
        
    except Exception as e:
        logger.error(f"Error convirtiendo PDF: {str(e)}")
        return []

def create_zip_file(image_paths: list, zip_path: str) -> bool:
    """Crea un ZIP con las imágenes"""
    try:
        logger.info(f"Creando ZIP: {zip_path}")
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for i, image_path in enumerate(image_paths):
                if os.path.exists(image_path):
                    # Nombre simple en el ZIP
                    arcname = f'page_{i+1:03d}.jpg'
                    zipf.write(image_path, arcname)
                    logger.info(f"Agregado al ZIP: {arcname}")
        
        # Verificar ZIP
        if os.path.exists(zip_path) and os.path.getsize(zip_path) > 0:
            logger.info(f"ZIP creado exitosamente: {os.path.getsize(zip_path)} bytes")
            return True
        else:
            logger.error("ZIP creado está vacío o no existe")
            return False
            
    except Exception as e:
        logger.error(f"Error creando ZIP: {str(e)}")
        return False

def cleanup_directory(directory: str):
    """Limpia un directorio temporal"""
    try:
        if os.path.exists(directory):
            shutil.rmtree(directory)
            logger.info(f"Directorio limpiado: {directory}")
    except Exception as e:
        logger.warning(f"No se pudo limpiar directorio {directory}: {str(e)}")

@app.get("/")
async def root():
    return {
        "message": "PDF2JPG Service v2.0",
        "endpoints": {
            "/convert/": "POST - Convert PDF to JPG images",
            "/health": "GET - Health check"
        },
        "usage": {
            "file_upload": "Send PDF file as 'pdf' in multipart/form-data",
            "url_download": "Send PDF URL as 'pdf_url' in form data"
        }
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "2.0.0",
        "features": ["file_upload", "url_download"]
    }

@app.post("/convert/")
async def convert_pdf(
    pdf: Optional[UploadFile] = File(None),
    pdf_url: Optional[str] = Form(None)
):
    """
    Convierte PDF a imágenes JPG y devuelve un ZIP
    
    Parámetros:
    - pdf: Archivo PDF (multipart/form-data)
    - pdf_url: URL del PDF a descargar
    """
    
    # Validar que se envió uno de los dos parámetros
    if not pdf and not pdf_url:
        raise HTTPException(
            status_code=422, 
            detail="Debe proporcionar 'pdf' (archivo) o 'pdf_url' (URL)"
        )
    
    if pdf and pdf_url:
        raise HTTPException(
            status_code=422,
            detail="Proporcione solo 'pdf' o 'pdf_url', no ambos"
        )
    
    # Crear directorio temporal único
    session_id = str(uuid.uuid4())
    temp_dir = os.path.join(TEMP_DIR, session_id)
    os.makedirs(temp_dir, exist_ok=True)
    
    pdf_path = None
    zip_path = None
    
    try:
        # Procesar según el método de entrada
        if pdf_url:
            # Método URL
            logger.info(f"Procesando URL: {pdf_url}")
            pdf_path = os.path.join(temp_dir, "input.pdf")
            
            if not download_pdf_from_url(pdf_url, pdf_path):
                raise HTTPException(
                    status_code=400,
                    detail="No se pudo descargar el PDF desde la URL"
                )
                
        else:
            # Método archivo
            logger.info(f"Procesando archivo: {pdf.filename}")
            
            # Validar tipo de archivo
            if not pdf.filename.lower().endswith('.pdf'):
                raise HTTPException(
                    status_code=400,
                    detail="El archivo debe ser PDF"
                )
            
            # Guardar archivo subido
            pdf_path = os.path.join(temp_dir, "input.pdf")
            
            content = await pdf.read()
            if len(content) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="El archivo PDF está vacío"
                )
                
            with open(pdf_path, "wb") as f:
                f.write(content)
        
        # Verificar que el PDF existe y tiene contenido
        if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) == 0:
            raise HTTPException(
                status_code=400,
                detail="PDF inválido o vacío"
            )
        
        # Convertir PDF a imágenes
        image_paths = convert_pdf_to_images(pdf_path, temp_dir)
        
        if not image_paths:
            raise HTTPException(
                status_code=500,
                detail="No se pudieron generar imágenes del PDF"
            )
        
        # Crear ZIP
        zip_path = os.path.join(temp_dir, "images.zip")
        if not create_zip_file(image_paths, zip_path):
            raise HTTPException(
                status_code=500,
                detail="No se pudo crear el archivo ZIP"
            )
        
        # Verificar ZIP final
        if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
            raise HTTPException(
                status_code=500,
                detail="El archivo ZIP está vacío o corrupto"
            )
        
        logger.info(f"Conversión exitosa: {len(image_paths)} imágenes, ZIP: {os.path.getsize(zip_path)} bytes")
        
        # Devolver archivo ZIP
        return FileResponse(
            path=zip_path,
            media_type='application/zip',
            filename='converted_images.zip',
            background=lambda: cleanup_directory(temp_dir)  # Limpiar después de enviar
        )
        
    except HTTPException:
        # Re-lanzar excepciones HTTP
        cleanup_directory(temp_dir)
        raise
        
    except Exception as e:
        # Manejar errores inesperados
        logger.error(f"Error inesperado: {str(e)}")
        cleanup_directory(temp_dir)
        raise HTTPException(
            status_code=500,
            detail=f"Error interno del servidor: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
