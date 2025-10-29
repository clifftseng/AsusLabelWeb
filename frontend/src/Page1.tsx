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
const RECENT_PATH_KEY = 'analysis.recentPaths';

const STATUS_LABELS: Record<AnalysisStatus['status'], string> = {
  queued: '排隊中',
  running: '分析中',
  completed: '已完成',
  cancelled: '已取消',
  failed: '失敗',
};

const parseRecentPaths = (): string[] => {
  if (typeof window === 'undefined') {
    return [];
  }
  try {
    const raw = window.localStorage.getItem(RECENT_PATH_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return parsed.filter((item) => typeof item === 'string');
    }
  } catch (err) {
    // ignore parsing errors and fallback to empty list
  }
  return [];
};

const Page1: React.FC<Page1Props> = ({
  setAnalysisResults,
  analysisResults,
  setAnalysisPath,
  analysisPath,
}) => {
  const [networkPath, setNetworkPath] = useState<string>('O:\\AI\\projects\\AsusLabel');
  const [pdfFiles, setPdfFiles] = useState<PDFFile[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [loadingList, setLoadingList] = useState<boolean>(false);
  const [analyzing, setAnalyzing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [recentPaths, setRecentPaths] = useState<string[]>(() => parseRecentPaths());

  const pollingRef = useRef<number | null>(null);
  const jobPathRef = useRef<string>('');

  const clearPolling = useCallback(() => {
    if (pollingRef.current !== null) {
      window.clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  useEffect(() => () => clearPolling(), [clearPolling]);

  const rememberPath = useCallback((path: string) => {
    setRecentPaths((previous) => {
      const deduped = [path, ...previous.filter((item) => item !== path)].slice(0, 5);
      try {
        if (typeof window !== 'undefined') {
          window.localStorage.setItem(RECENT_PATH_KEY, JSON.stringify(deduped));
        }
      } catch (err) {
        // ignore storage errors
      }
      return deduped;
    });
  }, []);

  const resetAnalysis = useCallback(() => {
    clearPolling();
    setJobId(null);
    setStatus(null);
    setAnalyzing(false);
    setAnalysisResults([]);
    setAnalysisPath('');
  }, [clearPolling, setAnalysisPath, setAnalysisResults]);

  const fetchPdfList = useCallback(
    async (rawPath: string) => {
      const trimmedPath = rawPath.trim();
      if (!trimmedPath) {
        setError('請先輸入要掃描的來源資料夾。');
        return;
      }

      resetAnalysis();
      setLoadingList(true);
      setError(null);
      setPdfFiles([]);
      setSelectedFiles(new Set());
      setNetworkPath(trimmedPath);

      try {
        const response = await axios.post<PDFFile[]>(`${API_BASE_URL}/api/list-pdfs`, {
          path: trimmedPath,
        });
        const files = response.data;
        setPdfFiles(files);
        const initialSelected = new Set(files.map((item) => item.filename));
        setSelectedFiles(initialSelected);
        rememberPath(trimmedPath);
        if (files.length === 0) {
          setError('指定路徑內沒有任何 PDF 檔案。');
        }
      } catch (err) {
        if (axios.isAxiosError(err) && err.response) {
          const message =
            typeof err.response.data?.detail === 'string'
              ? err.response.data.detail
              : '無法載入 PDF 清單，請確認路徑是否存在並具有讀取權限。';
          setError(message);
        } else {
          setError('無法載入 PDF 清單，請檢查網路或伺服器狀態。');
        }
      } finally {
        setLoadingList(false);
      }
    },
    [rememberPath, resetAnalysis],
  );

  const handlePathChange = (event: ChangeEvent<HTMLInputElement>) => {
    setNetworkPath(event.target.value);
  };

  const handleLoadPdfs = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      await fetchPdfList(networkPath);
    },
    [fetchPdfList, networkPath],
  );

  const handleSelectRecentPath = useCallback(
    (path: string) => {
      void fetchPdfList(path);
    },
    [fetchPdfList],
  );

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

  const selectedFileList = useMemo(
    () => pdfFiles.filter((file) => selectedFiles.has(file.filename)),
    [pdfFiles, selectedFiles],
  );

  const handleStatusUpdate = useCallback(
    (nextStatus: AnalysisStatus) => {
      setStatus(nextStatus);
      const isActive = nextStatus.status === 'queued' || nextStatus.status === 'running';
      setAnalyzing(isActive);
      setAnalysisResults(nextStatus.results);

      if (nextStatus.results.length > 0 || nextStatus.download_ready) {
        setAnalysisPath(jobPathRef.current);
      }

      if (nextStatus.status === 'completed' || nextStatus.status === 'cancelled' || nextStatus.status === 'failed') {
        clearPolling();
      }

      if (nextStatus.status === 'failed' && nextStatus.error) {
        setError(nextStatus.error);
      }
    },
    [clearPolling, setAnalysisPath, setAnalysisResults],
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
          const message =
            typeof err.response.data?.detail === 'string'
              ? err.response.data.detail
              : '無法取得最新進度，請稍後再試。';
          setError(message);
        } else {
          setError('無法取得最新進度，請檢查網路或伺服器狀態。');
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
      setError('請先勾選欲分析的 PDF 檔案。');
      return;
    }

    const payloadFiles = selectedFileList.map((file) => ({
      id: file.id,
      filename: file.filename,
    }));

    setError(null);
    setAnalysisResults([]);
    setStatus(null);
    jobPathRef.current = networkPath.trim();

    try {
      setAnalyzing(true);
      const response = await axios.post<{ job_id: string; status?: AnalysisStatus['status'] }>(
        `${API_BASE_URL}/api/analyze/start`,
        {
          path: jobPathRef.current,
          files: payloadFiles,
        },
      );
      const nextJobId = response.data.job_id;
      setJobId(nextJobId);
      const initialStatus: AnalysisStatus = {
        job_id: nextJobId,
        status: response.data.status ?? 'queued',
        progress: 0,
        processed_count: 0,
        total_count: payloadFiles.length,
        results: [],
        download_ready: false,
        download_path: null,
        error: null,
        current_file: null,
        messages: ['工作已加入佇列，等待開始。'],
      };
      setStatus(initialStatus);
      startPolling(nextJobId);
    } catch (err) {
      setAnalyzing(false);
      if (axios.isAxiosError(err) && err.response) {
        const message =
          typeof err.response.data?.detail === 'string'
            ? err.response.data.detail
            : '無法啟動分析，請稍後再試或聯絡系統管理員。';
        setError(message);
      } else {
        setError('無法啟動分析，請檢查網路或伺服器狀態。');
      }
    }
  };

  const stopPollingWithStatus = useCallback(
    (nextStatus: AnalysisStatus) => {
      clearPolling();
      setAnalyzing(false);
      handleStatusUpdate(nextStatus);
    },
    [clearPolling, handleStatusUpdate],
  );

  const handleCancel = async () => {
    if (!jobId) {
      return;
    }
    try {
      const response = await axios.post<AnalysisStatus>(`${API_BASE_URL}/api/analyze/stop/${jobId}`);
      stopPollingWithStatus(response.data);
    } catch (err) {
      if (axios.isAxiosError(err) && err.response) {
        const message =
          typeof err.response.data?.detail === 'string'
            ? err.response.data.detail
            : '無法終止分析，請稍後再試。';
        setError(message);
      } else {
        setError('無法終止分析，請檢查網路或伺服器狀態。');
      }
    }
  };

  const isJobActive = status ? status.status === 'queued' || status.status === 'running' : analyzing;
  const canDownload = Boolean(status?.download_ready && jobId);
  const statusLabel = status ? STATUS_LABELS[status.status] ?? status.status.toUpperCase() : '';

  return (
    <div className="container mt-5">
      <h1 className="mb-4">批次標籤分析工具</h1>

      <form onSubmit={handleLoadPdfs} className="mb-4">
        <label htmlFor="networkPath" className="form-label fw-semibold">
          來源資料夾
        </label>
        <div className="input-group">
          <input
            id="networkPath"
            type="text"
            className="form-control"
            placeholder="請輸入來源資料夾，例如 \\\\server\\share"
            value={networkPath}
            onChange={handlePathChange}
            required
          />
          <button type="submit" className="btn btn-primary" disabled={loadingList}>
            {loadingList ? '載入中…' : '載入 PDF'}
          </button>
        </div>
      </form>

      {recentPaths.length > 0 && (
        <div className="mb-4">
          <span className="fw-semibold me-2">最近使用：</span>
          {recentPaths.map((path) => (
            <button
              key={path}
              type="button"
              className="btn btn-sm btn-outline-secondary me-2 mb-2"
              onClick={() => handleSelectRecentPath(path)}
              disabled={loadingList || isJobActive}
            >
              {path}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}

      {pdfFiles.length > 0 && (
        <div className="mb-4">
          <h2 className="mb-3">PDF 檔案 ({pdfFiles.length})</h2>
          <p className="text-muted">已勾選 {selectedFileList.length} 筆檔案</p>
          <div className="table-responsive">
            <table className="table table-striped align-middle">
              <thead>
                <tr>
                  <th scope="col" style={{ width: '80px' }}>
                    #
                  </th>
                  <th scope="col">檔名</th>
                  <th scope="col" style={{ width: '140px' }}>
                    加入分析
                  </th>
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
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="d-flex flex-wrap gap-3">
            <button
              className="btn btn-success"
              onClick={handleAnalyze}
              disabled={isJobActive || selectedFileList.length === 0}
              type="button"
            >
              {isJobActive ? '分析中…' : '開始分析'}
            </button>
            {isJobActive && (
              <button className="btn btn-outline-danger" type="button" onClick={handleCancel}>
                終止分析
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
          <div
            className="progress mb-2"
            role="progressbar"
            aria-valuenow={Math.round(status.progress)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div className="progress-bar" style={{ width: `${Math.round(status.progress)}%` }}>
              {Math.round(status.progress)}%
            </div>
          </div>
          <p className="mb-1">
            狀態：<strong className="ms-1">{statusLabel}</strong>
          </p>
          <p className="mb-3">
            處理進度：{status.processed_count} / {status.total_count}
            {status.current_file ? `（目前處理：${status.current_file}）` : ''}
          </p>
          <div className="bg-light border rounded p-3" data-testid="analysis-log">
            <h3 className="h6 mb-2">即時訊息</h3>
            {status.messages.length > 0 ? (
              <ul className="mb-0">
                {status.messages.map((message, index) => (
                  <li key={`${index}-${message}`}>{message}</li>
                ))}
              </ul>
            ) : (
              <p className="text-muted mb-0">暫無訊息</p>
            )}
          </div>
        </div>
      )}

      {analysisResults.length > 0 && (
        <div className="mt-5">
          <h2 className="mb-3">分析結果</h2>
          {analysisPath && (
            <p>
              來源路徑：<strong className="ms-1">{analysisPath}</strong>
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
