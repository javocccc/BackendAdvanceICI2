from pathlib import Path
from zipfile import BadZipFile, ZipFile

from rest_framework.exceptions import ValidationError


EXTENSIONES = {".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".txt", ".csv"}
MAX_ARCHIVOS = 20
MAX_BYTES_ARCHIVO = 10 * 1024 * 1024


def validar_documento(archivo):
    """Comprueba extensión, tamaño y contenido básico sin confiar en Content-Type."""
    nombre = archivo.name.replace("\\", "/").split("/")[-1]
    extension = Path(nombre).suffix.lower()
    if extension not in EXTENSIONES:
        raise ValidationError({"archivos": f"{nombre}: formato no admitido."})
    if not 0 < archivo.size < MAX_BYTES_ARCHIVO:
        raise ValidationError({"archivos": f"{nombre}: debe ser no vacío y menor de 10 MB."})

    try:
        archivo.seek(0)
        cabecera = archivo.read(8)
        if extension == ".pdf" and not cabecera.startswith(b"%PDF-"):
            raise ValueError("cabecera PDF inválida")
        if extension == ".png" and cabecera != b"\x89PNG\r\n\x1a\n":
            raise ValueError("cabecera PNG inválida")
        if extension in {".jpg", ".jpeg"} and not cabecera.startswith(b"\xff\xd8\xff"):
            raise ValueError("cabecera JPEG inválida")
        if extension in {".docx", ".xlsx"}:
            with ZipFile(archivo) as paquete:
                partes = set(paquete.namelist())
                esencial = "word/document.xml" if extension == ".docx" else "xl/workbook.xml"
                if "[Content_Types].xml" not in partes or esencial not in partes:
                    raise ValueError("estructura Office inválida")
        if extension in {".txt", ".csv"}:
            archivo.seek(0)
            contenido = archivo.read()
            contenido.decode("utf-8-sig")
            if b"\x00" in contenido:
                raise ValueError("contenido de texto inválido")
    except (BadZipFile, UnicodeDecodeError, ValueError) as error:
        raise ValidationError({"archivos": f"{nombre}: contenido no corresponde al formato {extension}."}) from error
    finally:
        archivo.seek(0)
    return nombre, extension.lstrip(".")
