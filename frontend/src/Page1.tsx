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
  const [networkPath, setNetworkPath] = useState<string>('');
  const [pdfFiles, setPdfFiles] = useState<PDFFile[]>([]);
  const [selectedLabelFile, setSelectedLabelFile] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  // const navigate = useNavigate(); // No longer needed

  const API_BASE_URL = 'http://localhost:8000'; // Backend API URL

  // Clear results when path changes or component mounts
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
    setSelectedLabelFile('');
    setAnalysisResults([]); // Clear previous analysis results
    setAnalysisPath(''); // Clear previous analysis path

    try {
      const response = await axios.post<PDFFile[]>(`${API_BASE_URL}/api/list-pdfs`, {
        path: networkPath,
      });
      setPdfFiles(response.data);
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

  const handleLabelFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    setSelectedLabelFile(e.target.value);
  };

  const handleAnalyze = async () => {
    if (!selectedLabelFile) {
      setError('請選擇一個 Label 檔案進行分析。');
      return;
    }
    setLoading(true);
    setError(null);

    try {
      const response = await axios.post<AnalysisResult[]>(`${API_BASE_URL}/api/analyze`, {
        path: networkPath,
        files: pdfFiles,
        label_filename: selectedLabelFile,
      });
      setAnalysisResults(response.data);
      setAnalysisPath(networkPath); // Save the path for display
      // navigate('/results'); // No longer navigate, display on same page
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
            {loading ? '載入中...' : '載入 PDF 檔案'}
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
                <th>是否為 Label 檔案</th>
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
                        type="radio"
                        name="labelFile"
                        id={`labelFile-${file.id}`}
                        value={file.filename}
                        checked={selectedLabelFile === file.filename}
                        onChange={handleLabelFileChange}
                      />
                      <label className="form-check-label" htmlFor={`labelFile-${file.id}`}>
                        選擇
                      </label>
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
              disabled={loading || !selectedLabelFile}
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