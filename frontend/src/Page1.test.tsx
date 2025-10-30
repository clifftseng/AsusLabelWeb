import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import axios from 'axios';
import Page1, { AnalysisResult } from './Page1';

jest.mock('axios', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
  },
}));

type AxiosMock = {
  get: jest.Mock;
  post: jest.Mock;
};

const mockedAxios = axios as unknown as AxiosMock;

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  close() {
    // noop
  }
}

const renderComponent = () => {
  const setAnalysisResults = jest.fn<void, [AnalysisResult[]]>();
  const setAnalysisPath = jest.fn<void, [string]>();
  render(
    <Page1
      analysisResults={[]}
      setAnalysisResults={setAnalysisResults}
      setAnalysisPath={setAnalysisPath}
      analysisPath=""
    />,
  );
  return { setAnalysisResults, setAnalysisPath };
};

beforeEach(() => {
  jest.clearAllMocks();
  MockEventSource.instances = [];
  (global as unknown as { EventSource: typeof EventSource }).EventSource =
    MockEventSource as unknown as typeof EventSource;
  window.localStorage.clear();
});

afterEach(() => {
  jest.useRealTimers();
});

test('creates a job and streams events to show results', async () => {
  jest.useFakeTimers();

  const pdfList = [
    { id: 1, filename: 'doc1.pdf' },
    { id: 2, filename: 'doc2.pdf' },
  ];

  const jobSummary = {
    job_id: 'job-123456',
    owner_id: 'tester',
    source_path: 'C:/data',
    status: 'queued' as const,
    progress: 0,
    total_files: 2,
    processed_files: 0,
    current_file: null,
    error: null,
    download_path: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };

  const jobDetailQueued = {
    ...jobSummary,
    events: [],
    input_manifest: [{ filename: 'doc1.pdf' }, { filename: 'doc2.pdf' }],
    output_manifest: [],
  };

  const jobDetailCompleted = {
    ...jobSummary,
    status: 'completed' as const,
    progress: 100,
    processed_files: 2,
    download_path: '/api/jobs/job-123456/download',
    events: [
      {
        event_id: 1,
        created_at: new Date().toISOString(),
        level: 'info',
        message: 'Job queued',
        metadata: {},
      },
      {
        event_id: 2,
        created_at: new Date().toISOString(),
        level: 'info',
        message: 'Job completed',
        metadata: {},
      },
    ],
    input_manifest: [{ filename: 'doc1.pdf' }, { filename: 'doc2.pdf' }],
    output_manifest: [
      {
        id: 1,
        filename: 'doc1.pdf',
        model_name: 'Model A',
        voltage: '12V',
        typ_batt_capacity_wh: '50',
        typ_capacity_mah: '4000',
        rated_capacity_mah: '3900',
        rated_energy_wh: '48',
      },
      {
        id: 2,
        filename: 'doc2.pdf',
        model_name: 'Model B',
        voltage: '11V',
        typ_batt_capacity_wh: '45',
        typ_capacity_mah: '3800',
        rated_capacity_mah: '3600',
        rated_energy_wh: '42',
      },
    ],
  };

  let detailCallCount = 0;

  mockedAxios.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/api/jobs')) {
      return { data: [jobSummary] };
    }
    if (url.endsWith('/api/jobs/job-123456')) {
      detailCallCount += 1;
      return detailCallCount === 1
        ? { data: jobDetailQueued }
        : { data: jobDetailCompleted };
    }
    throw new Error(`Unexpected GET ${url}`);
  });

  mockedAxios.post.mockImplementation(async (url: string) => {
    if (url.endsWith('/api/list-pdfs')) {
      return { data: pdfList };
    }
    if (url.endsWith('/api/jobs')) {
      return { data: jobSummary };
    }
    throw new Error(`Unexpected POST ${url}`);
  });

  const { setAnalysisResults, setAnalysisPath } = renderComponent();

  // owner id input
  const ownerInput = await screen.findByPlaceholderText('請輸入使用者代號');
  fireEvent.change(ownerInput, { target: { value: 'tester' } });

  const pathInput = screen.getByPlaceholderText('請輸入來源資料夾');
  fireEvent.change(pathInput, { target: { value: 'C:/data' } });

  const loadButton = screen.getByRole('button', { name: '載入 PDF' });
  await act(async () => {
    fireEvent.click(loadButton);
  });

  expect(await screen.findByText('doc1.pdf')).toBeInTheDocument();

  const createButton = screen.getByRole('button', { name: '建立分析工作' });
  await act(async () => {
    fireEvent.click(createButton);
  });

  expect(mockedAxios.post).toHaveBeenCalledWith(
    `${process.env.REACT_APP_API_BASE_URL ?? 'http://localhost:8000'}/api/jobs`,
    expect.objectContaining({
      owner_id: 'tester',
      source_path: 'C:/data',
    }),
  );

  await waitFor(() => expect(detailCallCount).toBeGreaterThan(0));
  await waitFor(() => expect(MockEventSource.instances.length).toBe(1));

  const eventSource = MockEventSource.instances[0];
  expect(eventSource.url).toContain('/api/jobs/job-123456/events');
  expect(eventSource.url).toContain('owner_id=tester');

  await act(async () => {
    eventSource.onmessage?.({
      data: JSON.stringify({
        event_id: 2,
        created_at: new Date().toISOString(),
        level: 'info',
        message: 'Job completed',
        metadata: {},
      }),
    } as MessageEvent);
    jest.runOnlyPendingTimers();
  });

  await waitFor(() => expect(setAnalysisResults).toHaveBeenCalled());
  expect(setAnalysisResults).toHaveBeenLastCalledWith(jobDetailCompleted.output_manifest);
  expect(setAnalysisPath).toHaveBeenLastCalledWith('C:/data');
  expect(await screen.findByText('Job completed')).toBeInTheDocument();
  expect(screen.getByText('Model A')).toBeInTheDocument();
  const downloadLink = screen.getByRole('link', { name: '下載結果' }) as HTMLAnchorElement;
  expect(downloadLink.href).toContain('owner_id=tester');
});

test('cancels a running job', async () => {
  jest.useFakeTimers();

  const runningJob = {
    job_id: 'job-running',
    owner_id: 'tester',
    source_path: 'C:/data',
    status: 'running' as const,
    progress: 10,
    total_files: 1,
    processed_files: 0,
    current_file: 'doc.pdf',
    error: null,
    download_path: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };

  const runningDetail = {
    ...runningJob,
    events: [],
    input_manifest: [{ filename: 'doc.pdf' }],
    output_manifest: [],
  };

  mockedAxios.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/api/jobs')) {
      return { data: [runningJob] };
    }
    if (url.endsWith('/api/jobs/job-running')) {
      return { data: runningDetail };
    }
    throw new Error(`Unexpected GET ${url}`);
  });

  mockedAxios.post.mockImplementation(async (url: string) => {
    if (url.endsWith('/cancel')) {
      return { data: runningDetail };
    }
    if (url.endsWith('/api/list-pdfs')) {
      return { data: [] };
    }
    return { data: runningDetail };
  });

  renderComponent();

  const ownerInput = await screen.findByPlaceholderText('請輸入使用者代號');
  fireEvent.change(ownerInput, { target: { value: 'tester' } });

  await waitFor(() => expect(screen.getByText('job-running'.slice(0, 8))).toBeInTheDocument());

  const jobButton = screen.getByText('job-running'.slice(0, 8)).closest('button');
  expect(jobButton).not.toBeNull();
  fireEvent.click(jobButton as HTMLButtonElement);

  const cancelButton = await screen.findByRole('button', { name: '終止工作' });
  await act(async () => {
    fireEvent.click(cancelButton);
  });

  const cancelCall = mockedAxios.post.mock.calls.find(([url]) =>
    url.endsWith('/api/jobs/job-running/cancel'),
  );
  expect(cancelCall).toBeDefined();
});
