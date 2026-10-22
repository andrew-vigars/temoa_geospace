import os
import hashlib
import logging
import datetime
import shutil
from pathlib import Path
from uuid import uuid4
from canoe_lake.catalog import get_connection, PROJECT_ROOT

logger = logging.getLogger(__name__)

# Registry of source ingestors
INGESTORS = {}

def register_ingestor(source_name: str):
    def decorator(func):
        INGESTORS[source_name] = func
        return func
    return decorator

def compute_file_hash(file_path: Path) -> str:
    """Computes SHA-256 hash of a file or directory."""
    if file_path.is_dir():
        # Quick hash of directory contents
        sha256 = hashlib.sha256()
        for f in sorted(file_path.iterdir()):
            if f.is_file():
                sha256.update(f.name.encode('utf-8'))
                sha256.update(str(f.stat().st_mtime).encode('utf-8'))
        return sha256.hexdigest()
        
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

def check_idempotency(source_name: str, collection_date: datetime.date = None, file_hash: str = None) -> bool:
    """Returns True if a record already exists matching the given criteria AND the physical file/folder still exists."""
    try:
        conn = get_connection()
        if collection_date and not file_hash:
            # Pre-download check
            res = conn.execute(
                "SELECT file_path FROM bronze_sources WHERE source_name = ? AND collection_date = ?",
                (source_name, collection_date)
            ).fetchone()
            if res:
                # Ensure the file/folder hasn't been manually deleted
                if (PROJECT_ROOT / res[0]).exists():
                    return True
                else:
                    logger.warning(f"Catalog record exists for {source_name} but file is missing. Forcing re-ingestion.")
                    return False
            return False
        elif file_hash:
            # Post-download hash check
            res = conn.execute(
                "SELECT file_path FROM bronze_sources WHERE source_name = ? AND file_hash = ?",
                (source_name, file_hash)
            ).fetchone()
            if res:
                if (PROJECT_ROOT / res[0]).exists():
                    return True
            return False
    except Exception as e:
        logger.warning(f"Could not check idempotency (is catalog initialized?): {e}")
    return False

def ingest_bronze(source_name: str):
    logger.info(f"Starting Bronze ingestion for source: {source_name}")
    
    if source_name not in INGESTORS:
        logger.error(f"Unknown source '{source_name}'. No ingestor registered.")
        return
        
    today = datetime.date.today()
    if check_idempotency(source_name, collection_date=today):
        logger.info(f"Skipping ingestion: Bronze record for {source_name} already exists for {today}.")
        return

    ingestor_func = INGESTORS[source_name]
    
    today_str = today.strftime("%Y-%m-%d")
    output_dir = PROJECT_ROOT / "data-lake" / "bronze" / today_str / source_name
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Downloading data to {output_dir}")
    
    try:
        metadata = ingestor_func(output_dir)
    except Exception as e:
        logger.error(f"Ingestion failed for {source_name}: {e}")
        return
        
    file_path = metadata.get("file_path")
    if not file_path or not Path(file_path).exists():
        logger.error(f"Ingestor for {source_name} did not return a valid file_path.")
        return
        
    try:
        file_hash = compute_file_hash(Path(file_path))
        logger.info(f"Computed file hash: {file_hash}")
    except Exception as e:
        logger.error(f"Failed to compute hash for {file_path}: {e}")
        return

    if check_idempotency(source_name, file_hash=file_hash):
        logger.info(f"Skipping catalog registration: Bronze record for {source_name} with identical file hash already exists.")
        return
        
    # Write to Catalog
    try:
        conn = get_connection()
        record_id = str(uuid4())
        collected_by = os.environ.get("CANOE_USER", "unknown")
        rel_file_path = str(Path(file_path).relative_to(PROJECT_ROOT)).replace("\\", "/")
        
        conn.execute("""
            INSERT INTO bronze_sources (
                id, source_name, source_type, source_url_or_doc_id, collection_date,
                collected_by, file_path, file_hash, file_format, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record_id,
            source_name,
            metadata.get("source_type", "unknown"),
            metadata.get("source_url_or_doc_id", None),
            today,
            collected_by,
            rel_file_path,
            file_hash,
            metadata.get("file_format", "unknown"),
            metadata.get("notes", None)
        ))
        
        logger.info(f"Successfully registered Bronze catalog record for {source_name} (ID: {record_id})")
    except Exception as e:
        logger.error(f"Failed to write catalog record for {source_name}: {e}")
        return

def register_manual_bronze(source_name: str, file_path: str, collection_date: str, doc_id: str = None, pub_date: str = None, notes: str = None):
    """Registers a manually downloaded file into the Bronze layer."""
    logger.info(f"Starting manual registration for {source_name}")
    
    src_path = Path(file_path)
    if not src_path.exists():
        logger.error(f"Source file not found: {file_path}")
        return
        
    try:
        col_date = datetime.datetime.strptime(collection_date, "%Y-%m-%d").date()
    except ValueError:
        logger.error(f"Invalid collection date format: {collection_date}. Use YYYY-MM-DD.")
        return

    p_date = None
    if pub_date:
        try:
            p_date = datetime.datetime.strptime(pub_date, "%Y-%m-%d").date()
        except ValueError:
            logger.error(f"Invalid publication date format: {pub_date}. Use YYYY-MM-DD.")
            return

    output_dir = PROJECT_ROOT / "data-lake" / "bronze" / source_name / collection_date
    output_dir.mkdir(parents=True, exist_ok=True)
    
    dest_path = output_dir / src_path.name
    logger.info(f"Copying {src_path} to {dest_path}")
    shutil.copy2(src_path, dest_path)
    
    try:
        # Compute file hash
        file_hash = "unknown"
        if os.path.exists(dest_path):
            if os.path.isdir(dest_path):
                # hash the json file if it exists, else hardcode
                json_path = os.path.join(dest_path, "rninja_snapshot.json")
                if os.path.exists(json_path):
                    with open(json_path, "rb") as f:
                        file_hash = hashlib.sha256(f.read()).hexdigest()
                else:
                    file_hash = "dir_hash"
            else:
                with open(dest_path, "rb") as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()
            logger.info(f"Computed file hash: {file_hash}")
    except Exception as e:
        logger.error(f"Failed to compute hash for {dest_path}: {e}")
        return

    if check_idempotency(source_name, file_hash=file_hash):
        logger.info(f"Skipping catalog registration: Bronze record for {source_name} with identical file hash already exists.")
        return

    # Write to Catalog
    try:
        conn = get_connection()
        record_id = str(uuid4())
        collected_by = os.environ.get("CANOE_USER", "unknown")
        rel_file_path = str(dest_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        
        conn.execute("""
            INSERT INTO bronze_sources (
                id, source_name, source_type, source_url_or_doc_id, collection_date,
                publication_date, collected_by, file_path, file_hash, file_format, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record_id,
            source_name,
            "manual",
            doc_id,
            col_date,
            p_date,
            collected_by,
            rel_file_path,
            file_hash,
            dest_path.suffix.lower().strip("."),
            notes
        ))
        
        logger.info(f"Successfully registered manual Bronze catalog record for {source_name} (ID: {record_id})")
    except Exception as e:
        logger.error(f"Failed to write catalog record for {source_name}: {e}")
        return
