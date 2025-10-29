import React, {
  ChangeEvent,
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import axios from 'axios';

interface PDFFile {
  id: number;
  filename: string;
  is_label: boolean;
}

export interface AnalysisResult {
  id: number;
  filename: string;
  model_name: string;
  voltage: string;
  typ_batt_capacity_wh: string;
  typ_capacity_mah: string;
  rated_capacity_mah: string;
  rated_energy_wh: string;
}

interface AnalysisStatus {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'cancelled' | 'failed';
  progress: number;
  processed_count: number;
  total_count: number;
  results: AnalysisResult[];
  download_ready: boolean;
  download_path: string | null;
  error: string | null;
  current_file: string | null;
  messages: string[];
}

interface Page1Props {
  setAnalysisResults: (results: AnalysisResult[]) => void;
  analysisResults: AnalysisResult[];
  setAnalysisPath: (path: string) => void;
  analysisPath: string;
}

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL ?? 'http://localhost:8000';
const POLL_INTERVAL_MS = 500;

const Page1: React.FC<Page1Props> = ({
  setAnalysisResults,
  analysisResults,
  setAnalysisPath,
  analysisPath,
}) => {
  const [networkPath, setNetworkPath] = useState<string>('O:\\AI\\projects\\AsusLabel');
  const [pdfFiles, setPdfFiles] = useState<PDFFile[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [labelFilename, setLabelFilename] = useState<string>('');
  const [loadingList, setLoadingList] = useState<boolean>(false);
  const [analyzing, setAnalyzing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<AnalysisStatus | null>(null);

  const pollingRef = useRef<number | null>(null);

  const clearPolling = useCallback(() => {
    if (pollingRef.current !== null) {
      window.clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      clearPolling();
    };
  }, [clearPolling]);

  const resetAnalysis = useCallback(() => {
    clearPolling();
    setJobId(null);
    setStatus(null);
    setAnalyzing(false);
    setAnalysisResults([]);
    setAnalysisPath('');
  }, [clearPolling, setAnalysisPath, setAnalysisResults]);

  const handlePathChange = (event: ChangeEvent<HTMLInputElement>) => {
    setNetworkPath(event.target.value);
  };

  const handleLoadPdfs = async (event: FormEvent) => {
    event.preventDefault();
    resetAnalysis();
    setLoadingList(true);
    setError(null);
    setPdfFiles([]);
    setSelectedFiles(new Set());
    setLabelFilename('');

    try {
      const response = await axios.post<PDFFile[]>(`${API_BASE_URL}/api/list-pdfs`, {
        path: networkPath,
      });
      const files = response.data;
      setPdfFiles(files);
      const initialSelected = new Set(files.map((item) => item.filename));
      setSelectedFiles(initialSelected);
      const label = files.find((item) => item.is_label)?.filename ?? '';
      setLabelFilename(label);
    } catch (err) {
      if (axios.isAxiosError(err) && err.response) {
        const message = typeof err.response.data?.detail === 'string' ? err.response.data.detail : '載入 PDF 清單失敗。';
        setError(message);
      } else {
        setError('載入 PDF 清單時發生未知錯誤。');
      }
    } finally {
      setLoadingList(false);
    }
  };

  const toggleFileSelection = (filename: string) => {
    setSelectedFiles((previous) => {
      const next = new Set(previous);
      if (next.has(filename)) {
        next.delete(filename);
      } else {
        next.add(filename);
      }
      return next;
    });
  };

  const handleLabelChange = (event: ChangeEvent<HTMLInputElement>) => {
    setLabelFilename(event.target.value);
  };

  const selectedFileList = useMemo(
    () => pdfFiles.filter((file) => selectedFiles.has(file.filename)),
    [pdfFiles, selectedFiles],
  );

  const stopPollingWithStatus = useCallback(
    (nextStatus: AnalysisStatus) => {
      clearPolling();
      setAnalyzing(false);
      setStatus(nextStatus);
    },
    [clearPolling],
  );

  const handleStatusUpdate = useCallback(
    (nextStatus: AnalysisStatus) => {
      setStatus(nextStatus);
      setAnalyzing(nextStatus.status === 'queued' || nextStatus.status === 'running');

      if (nextStatus.status === 'completed') {
        setAnalysisResults(nextStatus.results);
        setAnalysisPath(networkPath);
        clearPolling();
      } else if (nextStatus.status === 'failed' && nextStatus.error) {
        setError(nextStatus.error);
        clearPolling();
      } else if (nextStatus.status === 'cancelled') {
        clearPolling();
      }
    },
    [clearPolling, networkPath, setAnalysisPath, setAnalysisResults],
  );

  const pollJobStatus = useCallback(
    async (job: string) => {
      try {
        const response = await axios.get<AnalysisStatus>(`${API_BASE_URL}/api/analyze/status/${job}`);
        handleStatusUpdate(response.data);
      } catch (err) {
        clearPolling();
        setAnalyzing(false);
        if (axios.isAxiosError(err) && err.response) {
          const message = typeof err.response.data?.detail === 'string' ? err.response.data.detail : '查詢分析狀態失敗。';
          setError(message);
        } else {
          setError('查詢分析狀態時發生未知錯誤。');
        }
      }
    },
    [clearPolling, handleStatusUpdate],
  );

  const startPolling = useCallback(
    (job: string) => {
      pollJobStatus(job);
      pollingRef.current = window.setInterval(() => {
        pollJobStatus(job);
      }, POLL_INTERVAL_MS);
    },
    [pollJobStatus],
  );

  const handleAnalyze = async () => {
    if (selectedFileList.length === 0) {
      setError('請至少選擇一個 PDF 檔案。');
      return;
    }

    setError(null);
    setAnalysisResults([]);
    setAnalysisPath('');

    const payloadFiles = selectedFileList.map((file) => ({
      ...file,
      is_label: file.filename === labelFilename,
    }));
    const labelFile = payloadFiles.find((file) => file.is_label);

    try {
      setAnalyzing(true);
      const response = await axios.post<{ job_id: string }>(`${API_BASE_URL}/api/analyze/start`, {
        path: networkPath,
        files: payloadFiles,
        label_filename: labelFile ? labelFile.filename : null,
      });
      const nextJobId = response.data.job_id;
      setJobId(nextJobId);
      startPolling(nextJobId);
    } catch (err) {
      setAnalyzing(false);
      if (axios.isAxiosError(err) && err.response) {
        const message = typeof err.response.data?.detail === 'string' ? err.response.data.detail : '啟動分析失敗。';
        setError(message);
      } else {
        setError('啟動分析時發生未知錯誤。');
      }
    }
  };

  const handleCancel = async () => {
    if (!jobId) {
      return;
    }
    try {
      const response = await axios.post<AnalysisStatus>(`${API_BASE_URL}/api/analyze/stop/${jobId}`);
      stopPollingWithStatus(response.data);
    } catch (err) {
      if (axios.isAxiosError(err) && err.response) {
        const message = typeof err.response.data?.detail === 'string' ? err.response.data.detail : '停止分析失敗。';
        setError(message);
      } else {
        setError('停止分析時發生未知錯誤。');
      }
    }
  };

  const isJobActive = status ? status.status === 'queued' || status.status === 'running' : analyzing;
  const canDownload = status?.download_ready && jobId;

  return (
    <div className="container mt-5">
      <h1 className="mb-4">PDF 標籤分析</h1>

      <form onSubmit={handleLoadPdfs} className="mb-4">
        <div className="input-group mb-3">
          <input
            type="text"
            className="form-control"
            placeholder="請輸入來源路徑，例如 \\\\server\\share"
            value={networkPath}
            onChange={handlePathChange}
            required
          />
          <button type="submit" className="btn btn-primary" disabled={loadingList}>
            {loadingList ? '載入中...' : '載入檔案'}
          </button>
        </div>
      </form>

      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}

      {pdfFiles.length > 0 && (
        <div className="mb-4">
          <h2 className="mb-3">PDF 清單</h2>
          <div className="table-responsive">
            <table className="table table-striped">
              <thead>
                <tr>
                  <th>#</th>
                  <th>檔名</th>
                  <th>選擇</th>
                  <th>作為標籤</th>
                </tr>
              </thead>
              <tbody>
                {pdfFiles.map((file) => (
                  <tr key={file.id}>
                    <td>{file.id}</td>
                    <td>{file.filename}</td>
                    <td>
                      <input
                        type="checkbox"
                        className="form-check-input"
                        checked={selectedFiles.has(file.filename)}
                        onChange={() => toggleFileSelection(file.filename)}
                        disabled={isJobActive}
                      />
                    </td>
                    <td>
                      <input
                        type="radio"
                        name="label-file"
                        className="form-check-input"
                        value={file.filename}
                        checked={labelFilename === file.filename}
                        onChange={handleLabelChange}
                        disabled={!selectedFiles.has(file.filename) || isJobActive}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="d-flex gap-3">
            <button
              className="btn btn-success"
              onClick={handleAnalyze}
              disabled={isJobActive || selectedFileList.length === 0}
              type="button"
            >
              {isJobActive ? '分析中...' : '開始分析'}
            </button>
            {isJobActive && (
              <button className="btn btn-outline-danger" type="button" onClick={handleCancel}>
                停止分析
              </button>
            )}
            {canDownload && jobId && (
              <a
                className="btn btn-outline-primary"
                href={`${API_BASE_URL}/api/analyze/download/${jobId}`}
              >
                下載結果
              </a>
            )}
          </div>
        </div>
      )}

      {status && (
        <div className="mb-4">
          <h2 className="mb-3">分析進度</h2>
          <p>
            狀態：
            <strong className="ms-1">{status.status.toUpperCase()}</strong>
          </p>
          <p>
            進度：
            <strong className="ms-1">{Math.round(status.progress)}%</strong>
          </p>
          <p>
            已完成 {status.processed_count} / {status.total_count}{' '}
            {status.current_file && `（處理中：${status.current_file}）`}
          </p>
          {status.messages.length > 0 && (
            <div>
              <h3 className="h5">訊息紀錄</h3>
              <ul>
                {status.messages.map((message, index) => (
                  <li key={`${index}-${message}`}>{message}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {analysisResults.length > 0 && (
        <div className="mt-5">
          <h2 className="mb-3">分析結果</h2>
          {analysisPath && (
            <p>
              來源路徑：
              <strong className="ms-1">{analysisPath}</strong>
            </p>
          )}
          <div className="table-responsive">
            <table className="table table-bordered">
              <thead>
                <tr>
                  <th>#</th>
                  <th>檔名</th>
                  <th>Model Name</th>
                  <th>Voltage</th>
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
