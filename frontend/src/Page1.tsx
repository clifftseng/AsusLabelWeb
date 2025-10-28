import React, { useState, ChangeEvent, FormEvent, useEffect } from 'react';
import axios from 'axios';
// import { useNavigate } from 'react-router-dom'; // No longer needed

interface PDFFile {
  id: number;
  filename: string;
  is_label: boolean;
}

interface AnalysisResult {
  id: number;
  filename: string;
  model_name: string;
  voltage: string;
  typ_batt_capacity_wh: string;
  typ_capacity_mah: string;
  rated_capacity_mah: string;
  rated_energy_wh: string;
}

interface Page1Props {
  setAnalysisResults: (results: AnalysisResult[]) => void;
  analysisResults: AnalysisResult[];
  setAnalysisPath: (path: string) => void;
  analysisPath: string;
}

const Page1: React.FC<Page1Props> = ({ setAnalysisResults, analysisResults, setAnalysisPath, analysisPath }) => {
  const [networkPath, setNetworkPath] = useState<string>('O:\\AI\\projects\\AsusLabel');
  const [pdfFiles, setPdfFiles] = useState<PDFFile[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const API_BASE_URL = 'http://localhost:8000'; // Backend API URL

  useEffect(() => {
    setAnalysisResults([]);
    setAnalysisPath('');
  }, [networkPath, setAnalysisResults, setAnalysisPath]);

  const handlePathChange = (e: ChangeEvent<HTMLInputElement>) => {
    setNetworkPath(e.target.value);
  };

  const handleLoadPdfs = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setPdfFiles([]);
    setSelectedFiles([]);
    setAnalysisResults([]);
    setAnalysisPath('');

    try {
      const response = await axios.post<PDFFile[]>(`${API_BASE_URL}/api/list-pdfs`, {
        path: networkPath,
      });
      setPdfFiles(response.data);
      setSelectedFiles(response.data.map((file) => file.filename)); // Select all files by default
    } catch (err) {
      if (axios.isAxiosError(err) && err.response) {
        setError(err.response.data.detail || '載入 PDF 檔案失敗');
      } else {
        setError('載入 PDF 檔案時發生未知錯誤');
      }
      console.error('Error loading PDFs:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleCheckboxChange = (filename: string) => {
    setSelectedFiles((prevSelected) =>
      prevSelected.includes(filename)
        ? prevSelected.filter((f) => f !== filename)
        : [...prevSelected, filename]
    );
  };

  const handleAnalyze = async () => {
    const labelFile = pdfFiles.find((file) => file.is_label);
    if (!labelFile) {
        // Even though the user doesn't want to see the error, 
        // we should still check for the label file and send it to the backend.
        // If no label file is found, we can't proceed.
        // We will just not show the error message to the user.
        console.error('Label file not found in the PDF list.');
        // setError('在 PDF 列表中找不到 Label 檔案。');
        // return;
    }

    setLoading(true);
    setError(null);

    const filesToAnalyze = pdfFiles.filter((file) => selectedFiles.includes(file.filename));

    try {
      const response = await axios.post<AnalysisResult[]>(`${API_BASE_URL}/api/analyze`, {
        path: networkPath,
        files: filesToAnalyze,
        label_filename: labelFile ? labelFile.filename : '',
      });
      setAnalysisResults(response.data);
      setAnalysisPath(networkPath);
    } catch (err) {
      if (axios.isAxiosError(err) && err.response) {
        setError(err.response.data.detail || '分析失敗');
      } else {
        setError('分析時發生未知錯誤');
      }
      console.error('Error analyzing files:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container mt-5">
      <h1 className="mb-4">PDF 檔案分析工具</h1>

      <form onSubmit={handleLoadPdfs} className="mb-4">
        <div className="input-group mb-3">
          <input
            type="text"
            className="form-control"
            placeholder="請輸入網路路徑，例如 \\abc\def..."
            value={networkPath}
            onChange={handlePathChange}
            required
          />
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? '載入中...' : '檢查'}
          </button>
        </div>
      </form>

      {error && <div className="alert alert-danger" role="alert">{error}</div>}

      {pdfFiles.length > 0 && (
        <div className="mb-4">
          <h2>PDF 檔案列表</h2>
          <table className="table table-striped">
            <thead>
              <tr>
                <th>編號</th>
                <th>檔名</th>
                <th>選擇</th>
              </tr>
            </thead>
            <tbody>
              {pdfFiles.map((file) => (
                <tr key={file.id}>
                  <td>{file.id}</td>
                  <td>{file.filename}</td>
                  <td>
                    <div className="form-check">
                      <input
                        className="form-check-input"
                        type="checkbox"
                        checked={selectedFiles.includes(file.filename)}
                        onChange={() => handleCheckboxChange(file.filename)}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="text-center">
            <button
              className="btn btn-success mt-3"
              onClick={handleAnalyze}
              disabled={loading || selectedFiles.length === 0}
            >
              {loading ? '分析中...' : '分析'}
            </button>
          </div>
        </div>
      )}

      {/* Analysis Results Section (integrated from Page2) */}
      {analysisResults.length > 0 && (
        <div className="mt-5">
          <h1 className="mb-4">分析結果</h1>
          {analysisPath && <p>分析路徑: <strong>{analysisPath}</strong></p>}

          <div className="table-responsive">
            <table className="table table-striped table-bordered">
              <thead>
                <tr>
                  <th>編號</th>
                  <th>檔名</th>
                  <th>Model Name</th>
                  <th>電壓</th>
                  <th>Typ Batt Capacity Wh</th>
                  <th>Typ Capacity mAh</th>
                  <th>Rated Capacity mAh</th>
                  <th>Rated Energy Wh</th>
                </tr>
              </thead>
              <tbody>
                {analysisResults.map((result) => (
                  <tr key={result.id}>
                    <td>{result.id}</td>
                    <td>{result.filename}</td>
                    <td>{result.model_name}</td>
                    <td>{result.voltage}</td>
                    <td>{result.typ_batt_capacity_wh}</td>
                    <td>{result.typ_capacity_mah}</td>
                    <td>{result.rated_capacity_mah}</td>
                    <td>{result.rated_energy_wh}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

export default Page1;