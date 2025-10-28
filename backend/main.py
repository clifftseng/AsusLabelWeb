from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import os
import time # For simulating analysis delay

app = FastAPI()

# Configure CORS to allow communication from the frontend
origins = [
    "http://localhost",
    "http://localhost:3000",  # Assuming React dev server runs on port 3000
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class PDFFile(BaseModel):
    id: int
    filename: str
    is_label: bool = False

class ListPDFsRequest(BaseModel):
    path: str

class AnalyzeRequest(BaseModel):
    path: str
    files: List[PDFFile]
    label_filename: str

class AnalysisResult(BaseModel):
    id: int
    filename: str
    model_name: str
    voltage: str
    typ_batt_capacity_wh: str
    typ_capacity_mah: str
    rated_capacity_mah: str
    rated_energy_wh: str

@app.get("/")
async def read_root():
    return {"message": "Welcome to the ASUS Label Analysis Backend!"}

@app.post("/api/list-pdfs", response_model=List[PDFFile])
async def list_pdfs(request: ListPDFsRequest):
    """
    接收一個網路路徑，列出該路徑下第一層的所有 PDF 檔案。
    """
    target_path = request.path.replace('\\', '//').replace('\\', '/') # Normalize path for os module
    
    # Basic validation for path
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail=f"Path not found: {request.path}")
    if not os.path.isdir(target_path):
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {request.path}")

    pdf_files = []
    try:
        for i, entry in enumerate(os.listdir(target_path)):
            full_path = os.path.join(target_path, entry)
            if os.path.isfile(full_path) and entry.lower().endswith('.pdf'):
                pdf_files.append(PDFFile(id=i + 1, filename=entry))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing files: {str(e)}")
    
    return pdf_files

@app.post("/api/analyze", response_model=List[AnalysisResult])
async def analyze_files(request: AnalyzeRequest):
    """
    接收網路路徑、PDF 檔案列表及指定的 Label 檔案，模擬分析並回傳結果。
    """
    print(f"Received analysis request for path: {request.path}")
    print(f"Label file: {request.label_filename}")
    print(f"Files to analyze: {[f.filename for f in request.files]}")

    # Simulate analysis time
    time.sleep(2) 

    # Mock analysis results based on the provided files
    results = []
    for i, file in enumerate(request.files):
        # Generate some dummy data for demonstration
        results.append(AnalysisResult(
            id=i + 1,
            filename=file.filename,
            model_name=f"Model_{i+1}",
            voltage=f"{12 + i}V",
            typ_batt_capacity_wh=f"{50 + i*5}Wh",
            typ_capacity_mah=f"{4000 + i*100}mAh",
            rated_capacity_mah=f"{3800 + i*90}mAh",
            rated_energy_wh=f"{48 + i*4}Wh"
        ))
    return results

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
