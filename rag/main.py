from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
from pathlib import Path
from dotenv import load_dotenv
from langchain_community.document_loaders.pdf import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains import create_retrieval_chain

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI(title="Legal RAG API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://satark-ai.vercel.app",
        "https://satark-ai.onrender.com",
        "http://localhost:3000",
        "http://localhost:5174"
    ],
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Define paths
BASE_DIR = Path(__file__).parent
CHROMA_DB_DIR = BASE_DIR / "chroma_db"

# Initialize global variables
db = None
retrieval_chain = None

class Query(BaseModel):
    question: str

class DocumentUpdate(BaseModel):
    pdf_path: str

def initialize_db():
    """Initialize ChromaDB and return the database instance"""
    embedding_function = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )
    
    # If ChromaDB exists, load it regardless of PDF presence
    if CHROMA_DB_DIR.exists():
        print(f"Loading existing ChromaDB from {CHROMA_DB_DIR}...")
        return Chroma(
            persist_directory=str(CHROMA_DB_DIR),
            embedding_function=embedding_function
        )
    else:
        raise RuntimeError(
            f"ChromaDB directory not found at {CHROMA_DB_DIR}. "
            "Please run the indexing script locally first to create the embeddings database."
        )

def create_new_db(pdf_path: str):
    """Create a new ChromaDB from a PDF file"""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found at {pdf_path}")
    
    embedding_function = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )
    
    print(f"Creating new ChromaDB at {CHROMA_DB_DIR}...")
    loader = PyPDFLoader(str(pdf_path))
    docs = loader.load()
    
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200
    )
    documents = text_splitter.split_documents(docs)
    
    # Ensure the directory exists
    CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)
    
    db = Chroma.from_documents(
        documents,
        embedding_function,
        persist_directory=str(CHROMA_DB_DIR)
    )
    db.persist()
    return db

def setup_chain(db):
    """Set up the retrieval chain with ChatGroq"""
    chat_model = ChatGroq(
        model_name="mixtral-8x7b-32768",
        temperature=0,
        api_key=os.getenv("GROQ_API_KEY")
    )
    
    prompt = ChatPromptTemplate.from_template("""
    Answer the following question based only on the provided context.
    Think step by step before providing a detailed answer.
    Also make sure do not include like from the context provided or from database just provide a professional and sophisticated.
    I will tip you $1000 if the user finds the answer helpful.
    <context>
    {context}
    </context>
    Question: {input}""")
    
    doc_chain = create_stuff_documents_chain(chat_model, prompt)
    retriever = db.as_retriever()
    return create_retrieval_chain(retriever, doc_chain)

@app.on_event("startup")
async def startup_event():
    """Initialize the database and chain when the API starts"""
    global db, retrieval_chain
    try:
        db = initialize_db()
        retrieval_chain = setup_chain(db)
    except Exception as e:
        print(f"Error during initialization: {e}")
        raise e

@app.post("/qa")
async def answer_question(query: Query):
    """
    Endpoint to answer questions based on the legal document
    """
    if not db or not retrieval_chain:
        raise HTTPException(
            status_code=500,
            detail="System not properly initialized. Please check server logs."
        )
    
    try:
        response = retrieval_chain.invoke({"input": query.question})
        return {"answer": response['answer']}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing question: {str(e)}"
        )

@app.post("/update-documents")
async def update_documents(update: DocumentUpdate):
    """
    Endpoint to update the document database with a new PDF
    This should only be called in a development/staging environment
    """
    global db, retrieval_chain
    try:
        db = create_new_db(update.pdf_path)
        retrieval_chain = setup_chain(db)
        return {"status": "success", "message": "Document database updated successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating documents: {str(e)}"
        )

@app.get("/health")
async def health_check():
    """
    Endpoint to check if the service is running and properly initialized
    """
    return {
        "status": "healthy",
        "database_initialized": db is not None,
        "chain_initialized": retrieval_chain is not None
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)