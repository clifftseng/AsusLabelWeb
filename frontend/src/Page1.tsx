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

interface PDFFile {
  id: number;
  filename: string;
}

interface JobSummary {
  job_id: string;
  owner_id: string;
  source_path: string;
  status: JobStatus;
  display_name: string;
  progress: number;
  total_files: number;
  processed_files: number;
  current_file: string | null;
  error: string | null;
  download_path: string | null;
  created_at: string;
  updated_at: string;
}

interface JobEvent {
  event_id: number;
  created_at: string;
  level: string;
  message: string;
  metadata: Record<string, unknown>;
}

interface JobDetail extends JobSummary {
  input_manifest: Array<Record<string, unknown>>;
  output_manifest: AnalysisResult[];
  events: JobEvent[];
}

interface BatchDeleteResponse {
  deleted: number;
}

type JobStatus = 'queued' | 'running' | 'retrying' | 'completed' | 'failed' | 'cancelled';

interface Page1Props {
  setAnalysisResults: (results: AnalysisResult[]) => void;
  analysisResults: AnalysisResult[];
  setAnalysisPath: (path: string) => void;
  analysisPath: string;
}

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL ?? 'http://localhost:8000';
const POLL_JOBS_INTERVAL_MS = 5000;
const STATUS_LABELS: Record<JobStatus, string> = {
  queued: '等待中',
  retrying: '重試中',
  running: '執行中',
  completed: '已完成',
  cancelled: '已取消',
  failed: '失敗',
};
const FINAL_STATUSES: JobStatus[] = ['completed', 'failed', 'cancelled'];
const RECENT_PATH_KEY = 'analysis.recentPaths';
const MAX_RECENT_PATHS = 3;
export const DEFAULT_OWNER_ID = process.env.REACT_APP_DEFAULT_OWNER_ID ?? 'anonymous';

const parseStoredList = (key: string, fallback: string[] = []): string[] => {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return parsed
        .filter((item) => typeof item === 'string')
        .slice(0, MAX_RECENT_PATHS);
    }
  } catch (err) {
    // ignore malformed storage content
  }
  return fallback;
};

const formatTimestamp = (value: string) => {
  try {
    const date = new Date(value);
    return new Intl.DateTimeFormat('zh-TW', {
      hour12: false,
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(date);
  } catch (err) {
    return value;
  }
};

const formatJobTimestamp = (value: string) => {
  try {
    const date = new Date(value);
    return new Intl.DateTimeFormat('zh-TW', {
      hour12: false,
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(date);
  } catch (err) {
    return value;
  }
};

const manifestFilename = (entry: Record<string, unknown>): string => {
  const candidate =
    (entry as { filename?: unknown }).filename ??
    (entry as { name?: unknown }).name ??
    (entry as { source_path?: unknown }).source_path;
  return typeof candidate === 'string' ? candidate : '';
};

const Page1: React.FC<Page1Props> = ({
  setAnalysisResults,
  analysisResults,
  setAnalysisPath,
  analysisPath,
}) => {
  const [networkPath, setNetworkPath] = useState<string>('\\\\smpfile11.simplo.com.tw\\AI\\projects\\AsusLabel');
  const [recentPaths, setRecentPaths] = useState<string[]>(() => parseStoredList(RECENT_PATH_KEY));
  const [pdfFiles, setPdfFiles] = useState<PDFFile[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [jobList, setJobList] = useState<JobSummary[]>([]);
  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null);
  const [jobEvents, setJobEvents] = useState<JobEvent[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [loadingPdfs, setLoadingPdfs] = useState<boolean>(false);
  const [submittingJob, setSubmittingJob] = useState<boolean>(false);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [selectedJobIds, setSelectedJobIds] = useState<Set<string>>(new Set());
  const [nameFilter, setNameFilter] = useState<string>('');
  const [isRenaming, setIsRenaming] = useState<boolean>(false);
  const [renameValue, setRenameValue] = useState<string>('');

  const jobsPollRef = useRef<number | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const isMountedRef = useRef<boolean>(true);
  const previousSelectedJobIdRef = useRef<string | null>(null);

  const selectedJob = useMemo(
    () => jobList.find((job) => job.job_id === selectedJobId) ?? null,
    [jobList, selectedJobId],
  );

  const ownerIdSafe = DEFAULT_OWNER_ID;

  const rememberPath = useCallback((path: string) => {
    setRecentPaths((previous) => {
      const deduped = [path, ...previous.filter((item) => item !== path)].slice(
        0,
        MAX_RECENT_PATHS,
      );
      try {
        if (typeof window !== 'undefined') {
          window.localStorage.setItem(RECENT_PATH_KEY, JSON.stringify(deduped));
        }
      } catch (err) {
        // ignore storage issues
      }
      return deduped;
    });
  }, []);

  const fetchJobDetail = useCallback(
    async (jobId: string) => {
      try {
        const response = await axios.get<JobDetail>(`${API_BASE_URL}/api/jobs/${jobId}`, {
          params: { owner_id: ownerIdSafe },
        });
        if (!isMountedRef.current) return;
        setJobDetail(response.data);
        setJobEvents(response.data.events);
        setAnalysisResults(response.data.output_manifest);
        setAnalysisPath(response.data.source_path);
      } catch (err) {
        if (!isMountedRef.current) return;
        setAnalysisError('無法取得工作詳情，請稍後再試。');
      }
    },
    [ownerIdSafe, setAnalysisPath, setAnalysisResults],
  );

  const fetchJobs = useCallback(async () => {
    try {
      const response = await axios.get<JobSummary[]>(`${API_BASE_URL}/api/jobs`, {
        params: { owner_id: ownerIdSafe },
      });
      if (!isMountedRef.current) return;
      setJobList(response.data);
      setJobsError(null);

      if (selectedJobId) {
        const updatedJob = response.data.find((job) => job.job_id === selectedJobId);
        if (
          updatedJob &&
          FINAL_STATUSES.includes(updatedJob.status) &&
          (!jobDetail || jobDetail.updated_at !== updatedJob.updated_at)
        ) {
          fetchJobDetail(selectedJobId);
        }
      }
    } catch (err) {
      if (!isMountedRef.current) return;
      setJobsError('無法取得工作列表，請稍後再試。');
    }
  }, [fetchJobDetail, jobDetail, ownerIdSafe, selectedJobId]);

  const closeEventSource = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  const subscribeToJobEvents = useCallback(
    (jobId: string) => {
      closeEventSource();
      if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
        return;
      }
      const eventSource = new EventSource(
        `${API_BASE_URL}/api/jobs/${jobId}/events?owner_id=${encodeURIComponent(ownerIdSafe)}`,
      );
      eventSource.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as JobEvent;
          setJobEvents((previous) => {
            const exists = previous.some((item) => item.event_id === payload.event_id);
            if (exists) {
              return previous;
            }
            return [...previous, payload].sort((a, b) => a.event_id - b.event_id);
          });
          fetchJobDetail(jobId);
        } catch (err) {
          // ignore malformed payload
        }
      };
      eventSource.onerror = () => {
        eventSource.close();
        eventSourceRef.current = null;
      };
      eventSourceRef.current = eventSource;
    },
    [closeEventSource, fetchJobDetail, ownerIdSafe],
  );

  useEffect(() => {
    isMountedRef.current = true;
    fetchJobs();
    jobsPollRef.current = window.setInterval(fetchJobs, POLL_JOBS_INTERVAL_MS);
    return () => {
      isMountedRef.current = false;
      if (jobsPollRef.current !== null) {
        window.clearInterval(jobsPollRef.current);
        jobsPollRef.current = null;
      }
      closeEventSource();
    };
  }, [fetchJobs, closeEventSource]);

  useEffect(() => {
    if (!selectedJobId) {
      closeEventSource();
      setJobDetail(null);
      setJobEvents([]);
      return;
    }
    fetchJobDetail(selectedJobId);
    subscribeToJobEvents(selectedJobId);
  }, [selectedJobId, fetchJobDetail, subscribeToJobEvents, closeEventSource]);

  useEffect(() => {
    if (!jobDetail) return;
    if (FINAL_STATUSES.includes(jobDetail.status)) {
      closeEventSource();
    }
  }, [jobDetail, closeEventSource]);

  useEffect(() => {
    setSelectedJobIds((previous) => {
      const next = new Set<string>();
      jobList.forEach((job) => {
        if (previous.has(job.job_id)) {
          next.add(job.job_id);
        }
      });
      return next;
    });
  }, [jobList]);

  useEffect(() => {
    const currentJobId = selectedJob?.job_id ?? null;
    const previousJobId = previousSelectedJobIdRef.current;

    if (!currentJobId || !selectedJob) {
      previousSelectedJobIdRef.current = null;
      setRenameValue('');
      setIsRenaming(false);
      return;
    }

    if (currentJobId !== previousJobId) {
      previousSelectedJobIdRef.current = currentJobId;
      setRenameValue(selectedJob.display_name);
      setIsRenaming(false);
      return;
    }

    previousSelectedJobIdRef.current = currentJobId;

    if (!isRenaming) {
      setRenameValue(selectedJob.display_name);
    }
  }, [isRenaming, selectedJob]);

  const handlePathChange = (event: ChangeEvent<HTMLInputElement>) => {
    setNetworkPath(event.target.value);
  };

  const handleLoadPdfs = async () => {
    setLoadingPdfs(true);
    try {
      const response = await axios.post<PDFFile[]>(`${API_BASE_URL}/api/list-pdfs`, {
        path: networkPath,
      });
      setPdfFiles(response.data);
      setSelectedFiles(new Set(response.data.map((item) => item.filename)));
      rememberPath(networkPath);
      setAnalysisError(null);
    } catch (err) {
      setAnalysisError('載入 PDF 清單時發生錯誤，請確認路徑是否正確。');
    } finally {
      setLoadingPdfs(false);
    }
  };

  const handlePathSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await handleLoadPdfs();
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

  const handleAnalyze = async () => {
    if (selectedFiles.size === 0) {
      setAnalysisError('請選擇要分析的檔案。');
      return;
    }
    setSubmittingJob(true);
    try {
      const payload = {
        owner_id: ownerIdSafe,
        source_path: networkPath,
        files: Array.from(selectedFiles).map((filename) => ({ filename })),
      };
      const response = await axios.post<JobSummary>(`${API_BASE_URL}/api/jobs`, payload);
      setAnalysisError(null);
      setSelectedJobId(response.data.job_id);
      setJobDetail(null);
      setJobEvents([]);
      setSelectedFiles(new Set());
      fetchJobs();
    } catch (err) {
      setAnalysisError('建立分析工作失敗，請稍後再試。');
    } finally {
      setSubmittingJob(false);
    }
  };

  const handleCancel = async (jobId: string) => {
    try {
      await axios.post(
        `${API_BASE_URL}/api/jobs/${jobId}/cancel`,
        { reason: 'Cancel by user', cancelled_by: ownerIdSafe },
        { params: { owner_id: ownerIdSafe } },
      );
      fetchJobDetail(jobId);
      fetchJobs();
    } catch (err) {
      setAnalysisError('終止工作失敗，請稍後再試。');
    }
  };

  const downloadUrl = jobDetail?.download_path
    ? `${API_BASE_URL}/api/jobs/${jobDetail.job_id}/download?owner_id=${encodeURIComponent(
        ownerIdSafe,
      )}`
    : null;

  const statusLabel = selectedJob ? STATUS_LABELS[selectedJob.status] : '';
  const isJobRunning = Boolean(selectedJob && !FINAL_STATUSES.includes(selectedJob.status));

  const jobResults = jobDetail?.output_manifest ?? analysisResults;
  const jobFiles = jobDetail?.input_manifest ?? [];

  const filteredJobs = useMemo(() => {
    const term = nameFilter.trim().toLowerCase();
    if (!term) {
      return jobList;
    }
    return jobList.filter((job) => {
      const nameMatch = job.display_name.toLowerCase().includes(term);
      const timestampMatch = formatJobTimestamp(job.created_at).toLowerCase().includes(term);
      return nameMatch || timestampMatch;
    });
  }, [jobList, nameFilter]);

  const allJobsSelected =
    filteredJobs.length > 0 && filteredJobs.every((job) => selectedJobIds.has(job.job_id));
  const hasSelectedJobs = selectedJobIds.size > 0;

  const toggleJobSelection = useCallback((jobId: string) => {
    setSelectedJobIds((previous) => {
      const next = new Set(previous);
      if (next.has(jobId)) {
        next.delete(jobId);
      } else {
        next.add(jobId);
      }
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    setSelectedJobIds((previous) => {
      const next = new Set(previous);
      const everySelected = filteredJobs.every((job) => next.has(job.job_id));
      if (everySelected) {
        filteredJobs.forEach((job) => next.delete(job.job_id));
      } else {
        filteredJobs.forEach((job) => next.add(job.job_id));
      }
      return next;
    });
  }, [filteredJobs]);

  const handleDeleteSelected = useCallback(async () => {
    const ids = Array.from(selectedJobIds);
    if (ids.length === 0) {
      return;
    }
    try {
      await axios.post<BatchDeleteResponse>(`${API_BASE_URL}/api/jobs/batch-delete`, {
        job_ids: ids,
        owner_id: ownerIdSafe,
      });
      setJobsError(null);
      setJobList((previous) => previous.filter((job) => !ids.includes(job.job_id)));
      setSelectedJobIds(new Set());
      if (selectedJobId && ids.includes(selectedJobId)) {
        setSelectedJobId(null);
        setJobDetail(null);
        setJobEvents([]);
        setAnalysisResults([]);
        setAnalysisPath('');
      }
    } catch (err) {
      setJobsError('刪除工作失敗，請稍後再試。');
    }
  }, [
    ownerIdSafe,
    selectedJobId,
    selectedJobIds,
    setAnalysisPath,
    setAnalysisResults,
  ]);

  const handleRenameClick = useCallback(() => {
    setIsRenaming(true);
  }, []);

  const handleRenameCancel = useCallback(() => {
    setIsRenaming(false);
    setRenameValue(selectedJob?.display_name ?? '');
  }, [selectedJob]);

  const handleRenameSubmit = useCallback(async () => {
    if (!selectedJobId) {
      return;
    }
    const trimmed = renameValue.trim();
    if (!trimmed) {
      setRenameValue(selectedJob?.display_name ?? '');
      setIsRenaming(false);
      return;
    }
    try {
      const response = await axios.patch<JobSummary>(
        `${API_BASE_URL}/api/jobs/${selectedJobId}`,
        { display_name: trimmed },
        { params: { owner_id: ownerIdSafe } },
      );
      setJobList((previous) =>
        previous.map((job) =>
          job.job_id === selectedJobId
            ? {
                ...job,
                display_name: response.data.display_name,
                updated_at: response.data.updated_at,
              }
            : job,
        ),
      );
      setJobDetail((previous) =>
        previous && previous.job_id === selectedJobId
          ? {
              ...previous,
              display_name: response.data.display_name,
              updated_at: response.data.updated_at,
            }
          : previous,
      );
      setRenameValue(response.data.display_name);
      setAnalysisError(null);
      setIsRenaming(false);
    } catch (err) {
      setAnalysisError('更新工作名稱失敗，請稍後再試。');
    }
  }, [ownerIdSafe, renameValue, selectedJob, selectedJobId]);

  return (
    <div className="container py-4">
      <h1 className="mb-4">ASUS Label 分析平台</h1>

      <div className="row">
        <div className="col-lg-3 mb-4">
          <div className="card h-100">
            <div className="card-header">
              <strong>工作列表</strong>
            </div>
            <div className="card-body d-flex flex-column" style={{ minHeight: '550px' }}>
              <div className="d-flex gap-2 mb-3">
                <input
                  className="form-control"
                  placeholder="搜尋工作名稱或時間"
                  value={nameFilter}
                  onChange={(event) => setNameFilter(event.target.value)}
                />
                <button
                  className="btn btn-outline-secondary"
                  type="button"
                  onClick={() => setNameFilter('')}
                  disabled={!nameFilter}
                >
                  清除
                </button>
              </div>

              <div className="d-flex justify-content-between align-items-center mb-2">
                <div className="form-check">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    id="selectAllJobs"
                    checked={allJobsSelected}
                    onChange={toggleSelectAll}
                    disabled={filteredJobs.length === 0}
                  />
                  <label className="form-check-label" htmlFor="selectAllJobs">
                    全選
                  </label>
                </div>
                <button
                  className="btn btn-outline-danger btn-sm"
                  type="button"
                  onClick={handleDeleteSelected}
                  disabled={!hasSelectedJobs}
                >
                  刪除已選
                </button>
              </div>

              <div className="flex-grow-1 overflow-auto border rounded p-2 bg-light">
                {jobsError && <p className="text-danger mb-2">{jobsError}</p>}
                {filteredJobs.length === 0 ? (
                  <p className="text-muted">目前沒有符合條件的工作。</p>
                ) : (
                  <ul className="list-unstyled mb-0">
                    {filteredJobs.map((job) => (
                      <li key={job.job_id} className="mb-2">
                        <div className="d-flex align-items-start gap-2">
                          <div className="form-check mt-1">
                            <input
                              className="form-check-input"
                              type="checkbox"
                              checked={selectedJobIds.has(job.job_id)}
                              onChange={() => toggleJobSelection(job.job_id)}
                            />
                          </div>
                          <button
                            type="button"
                            className={`btn btn-sm w-100 text-start ${
                              selectedJobId === job.job_id ? 'btn-primary' : 'btn-outline-primary'
                            }`}
                            onClick={() => setSelectedJobId(job.job_id)}
                          >
                            <div className="d-flex justify-content-between">
                              <div className="me-2">
                                <div className="fw-semibold text-truncate">
                                  {job.display_name || formatJobTimestamp(job.created_at)}
                                </div>
                                <div className="small text-muted">
                                  {formatJobTimestamp(job.created_at)}
                                </div>
                              </div>
                              <div className="text-end">
                                <div>{STATUS_LABELS[job.status]}</div>
                                <div className="small text-muted">
                                  {Math.round(job.progress * 100)}% · {job.total_files} 檔
                                </div>
                              </div>
                            </div>
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        </div>

        <div className="col-lg-9">
          <div className="card mb-4">
            <div className="card-header">
              <strong>建立分析工作</strong>
            </div>
            <div className="card-body">
              <form className="row g-3 mb-3" onSubmit={handlePathSubmit}>
                <div className="col-md-8">
                  <label className="form-label" htmlFor="networkPath">
                    資料夾路徑
                  </label>
                  <input
                    id="networkPath"
                    className="form-control"
                    placeholder="請輸入來源資料夾"
                    value={networkPath}
                    onChange={handlePathChange}
                  />
                </div>
                <div className="col-md-4 d-flex align-items-end">
                  <button
                    className="btn btn-secondary w-100"
                    type="submit"
                    disabled={loadingPdfs}
                  >
                    {loadingPdfs ? '載入中…' : '載入 PDF'}
                  </button>
                </div>
              </form>

              {recentPaths.length > 0 && (
                <div className="mb-3">
                  <div className="d-flex flex-wrap gap-2">
                    {recentPaths.map((item) => (
                      <button
                        key={item}
                        type="button"
                        className="btn btn-outline-secondary btn-sm"
                        onClick={() => {
                          setNetworkPath(item);
                          setSelectedFiles(new Set());
                        }}
                      >
                        {item}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {pdfFiles.length > 0 && (
                <div className="table-responsive mb-3" style={{ maxHeight: '250px', overflowY: 'auto' }}>
                  <table className="table table-bordered table-sm">
                    <thead className="table-light">
                      <tr>
                        <th style={{ width: '60px' }}>#</th>
                        <th>檔名</th>
                        <th style={{ width: '70px' }}>選取</th>
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
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="d-flex gap-3 flex-wrap">
                <button
                  className="btn btn-success"
                  type="button"
                  onClick={handleAnalyze}
                  disabled={submittingJob || selectedFiles.size === 0}
                >
                  {submittingJob ? '建立中，請稍候' : '建立分析工作'}
                </button>
              </div>
              {analysisError && <p className="text-danger mt-3 mb-0">{analysisError}</p>}
            </div>
          </div>

          {jobDetail && jobFiles.length > 0 && (
            <div className="card mb-4">
              <div className="card-header">
                <strong>工作檔案列表</strong>
              </div>
              <div className="card-body">
                <div className="table-responsive" style={{ maxHeight: '300px', overflowY: 'auto' }}>
                  <table className="table table-striped table-sm">
                    <thead className="table-light">
                      <tr>
                        <th style={{ width: '60px' }}>#</th>
                        <th>檔名</th>
                      </tr>
                    </thead>
                    <tbody>
                      {jobFiles.map((entry, index) => (
                        <tr key={`${manifestFilename(entry)}-${index}`}>
                          <td>{index + 1}</td>
                          <td>{manifestFilename(entry)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}


          {selectedJob && (
            <div className="card mb-4">
              <div className="card-header d-flex flex-wrap justify-content-between align-items-center gap-2">
                <strong>工作詳情</strong>
                {(downloadUrl || isJobRunning) && (
                  <div className="d-flex flex-wrap gap-2">
                    {downloadUrl && (
                      <a className="btn btn-outline-primary btn-sm" href={downloadUrl}>
                        下載結果
                      </a>
                    )}
                    {isJobRunning && (
                      <button
                        className="btn btn-outline-danger btn-sm"
                        type="button"
                        onClick={() => handleCancel(selectedJob.job_id)}
                      >
                        終止工作
                      </button>
                    )}
                  </div>
                )}
              </div>
              <div className="card-body">
                <div className="mb-3">
                  <div className="d-flex justify-content-between align-items-center mb-2">
                    <div className="fw-semibold">工作名稱</div>
                    {!isRenaming && (
                      <button
                        className="btn btn-link btn-sm p-0"
                        type="button"
                        onClick={handleRenameClick}
                      >
                        重新命名
                      </button>
                    )}
                  </div>
                  {isRenaming ? (
                    <div className="d-flex flex-wrap gap-2">
                      <input
                        className="form-control form-control-sm"
                        value={renameValue}
                        onChange={(event) => setRenameValue(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') {
                            event.preventDefault();
                            handleRenameSubmit();
                          }
                        }}
                      />
                      <button className="btn btn-primary btn-sm" type="button" onClick={handleRenameSubmit}>
                        儲存
                      </button>
                      <button className="btn btn-outline-secondary btn-sm" type="button" onClick={handleRenameCancel}>
                        取消
                      </button>
                    </div>
                  ) : (
                    <div>{selectedJob.display_name || formatJobTimestamp(selectedJob.created_at)}</div>
                  )}
                </div>
                <div className="row mb-3">
                  <div className="col-md-4">
                    <div className="fw-semibold">Job ID</div>
                    <div>{selectedJob.job_id}</div>
                  </div>
                  <div className="col-md-4">
                    <div className="fw-semibold">狀態</div>
                    <div>{statusLabel}</div>
                  </div>
                  <div className="col-md-4">
                    <div className="fw-semibold">處理進度</div>
                    <div>
                      {selectedJob.processed_files}/{selectedJob.total_files} 檔 ·{' '}
                      {Math.round(selectedJob.progress * 100)}%
                    </div>
                  </div>
                </div>
                <div className="row mb-3">
                  <div className="col-md-4">
                    <div className="fw-semibold">建立時間</div>
                    <div>{formatJobTimestamp(selectedJob.created_at)}</div>
                  </div>
                  <div className="col-md-8">
                    <div className="fw-semibold">來源路徑</div>
                    <div className="text-break">{selectedJob.source_path}</div>
                  </div>
                </div>
                {selectedJob.error && (
                  <div className="alert alert-warning" role="alert">
                    {selectedJob.error}
                  </div>
                )}
              </div>
            </div>
          )}

          {jobResults.length > 0 && (
            <div className="card mb-4">
              <div className="card-header">
                <strong>分析結果</strong>
              </div>
              <div className="card-body">
                <div className="table-responsive" style={{ maxHeight: '400px', overflowY: 'auto' }}>
                  <table className="table table-striped table-bordered table-sm">
                    <thead className="table-light">
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
                      {jobResults.map((result) => (
                        <tr key={`${result.filename}-${result.id}`}>
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
            </div>
          )}

          {jobEvents.length > 0 && (
            <div className="card mb-4">
              <div className="card-header">
                <strong>工作事件</strong>
              </div>
              <div className="card-body bg-light" style={{ maxHeight: '300px', overflowY: 'auto' }}>
                <ul className="list-unstyled mb-0">
                  {jobEvents.map((event, index) => (
                    <li key={`${event.event_id ?? 'event'}-${event.created_at}-${index}`} className="mb-2">
                      <div className="small text-muted">{formatTimestamp(event.created_at)}</div>
                      <div
                        className={
                          event.level === 'error'
                            ? 'text-danger'
                            : event.level === 'warning'
                            ? 'text-warning'
                            : ''
                        }
                      >
                        {event.message}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default Page1;
