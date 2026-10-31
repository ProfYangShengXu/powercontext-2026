from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "seekdb"
LOG_DIR = BASE_DIR / "logs"
COLLECTION_NAME = "studydesk"

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
INGEST_BATCH_SIZE = 8
SEARCH_BRANCH_RESULTS = 10
SEARCH_TOP_K = 5
MAX_COURSE_LENGTH = 64

HOST = "127.0.0.1"
PORT = 8001

SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf"}
EMBEDDING_MODEL_DIR = (
    Path.home() / ".cache" / "pyseekdb" / "onnx_models" / "all-MiniLM-L6-v2" / "onnx"
)
REQUIRED_MODEL_FILES = (
    "model.onnx",
    "tokenizer.json",
    "config.json",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "vocab.txt",
)
