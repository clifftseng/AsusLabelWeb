import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import axios from 'axios';
import Page1, { AnalysisResult } from './Page1';

jest.mock('axios', () => ({
  __esModule: true,
  default: {
    post: jest.fn(),
    get: jest.fn(),
  },
}));

type AxiosMock = {
  post: jest.Mock;
  get: jest.Mock;
};

const mockedAxios = axios as unknown as AxiosMock;

const renderComponent = () => {
  const setAnalysisResults = jest.fn<void, [AnalysisResult[]]>();
  const setAnalysisPath = jest.fn<void, [string]>();
  const props = {
    setAnalysisResults,
    analysisResults: [] as AnalysisResult[],
    setAnalysisPath,
    analysisPath: '',
  };
  render(<Page1 {...props} />);
  return { setAnalysisResults, setAnalysisPath };
};

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  jest.clearAllMocks();
  jest.useRealTimers();
});

test('starts analysis and renders results when job completes', async () => {
  jest.useFakeTimers();

  const samplePdfs = [
    { id: 1, filename: 'doc.pdf' },
    { id: 2, filename: 'label.pdf' },
  ];

  mockedAxios.post.mockResolvedValueOnce({ data: samplePdfs });
  mockedAxios.post.mockResolvedValueOnce({ data: { job_id: 'job123', status: 'queued' } });

  const runningStatus = {
    job_id: 'job123',
    status: 'running' as const,
    progress: 40,
    processed_count: 1,
    total_count: 2,
    results: [
      {
        id: 1,
        filename: 'doc.pdf',
        model_name: 'Model_1',
        voltage: '12V',
        typ_batt_capacity_wh: '50Wh',
        typ_capacity_mah: '4000mAh',
        rated_capacity_mah: '3800mAh',
        rated_energy_wh: '48Wh',
      },
    ],
    download_ready: false,
    download_path: null,
    error: null,
    current_file: 'doc.pdf',
    messages: ['doc.pdf 分析完成 (1/2)'],
  };

  const completedStatus = {
    ...runningStatus,
    status: 'completed' as const,
    progress: 100,
    processed_count: 2,
    results: [
      ...runningStatus.results,
      {
        id: 2,
        filename: 'label.pdf',
        model_name: 'Model_2',
        voltage: '13V',
        typ_batt_capacity_wh: '55Wh',
        typ_capacity_mah: '4100mAh',
        rated_capacity_mah: '3900mAh',
        rated_energy_wh: '52Wh',
      },
    ],
    download_ready: true,
    download_path: '/fake/path.xlsx',
    current_file: null,
    messages: [...runningStatus.messages, '已匯出分析結果 analysis_result.xlsx'],
  };

  mockedAxios.get
    .mockResolvedValueOnce({ data: runningStatus })
    .mockResolvedValueOnce({ data: completedStatus });

  const { setAnalysisResults, setAnalysisPath } = renderComponent();

  const pathInput = screen.getByPlaceholderText(/請輸入來源資料夾/);
  fireEvent.change(pathInput, { target: { value: 'C:/data' } });

  const loadButton = screen.getByRole('button', { name: '載入 PDF' });
  await act(async () => {
    fireEvent.click(loadButton);
  });

  await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledTimes(1));
  expect(await screen.findByRole('heading', { name: 'PDF 檔案 (2)' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'C:/data' })).toBeInTheDocument();

  const analyzeButton = await screen.findByRole('button', { name: '開始分析' });
  await act(async () => {
    fireEvent.click(analyzeButton);
  });

  await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(mockedAxios.get).toHaveBeenCalledTimes(1));

  await act(async () => {
    jest.advanceTimersByTime(600);
  });

  await waitFor(() => expect(mockedAxios.get).toHaveBeenCalledTimes(2));

  await waitFor(() => expect(setAnalysisResults).toHaveBeenCalledWith(runningStatus.results));
  await waitFor(() => expect(setAnalysisResults).toHaveBeenCalledWith(completedStatus.results));
  expect(setAnalysisPath).toHaveBeenCalledWith('C:/data');

  expect(screen.getByText('100%')).toBeInTheDocument();
  expect(screen.getByText('doc.pdf 分析完成 (1/2)')).toBeInTheDocument();
  expect(screen.getByText('已匯出分析結果 analysis_result.xlsx')).toBeInTheDocument();
  expect(screen.getByTestId('analysis-log')).toHaveTextContent('doc.pdf 分析完成 (1/2)');
  expect(localStorage.getItem('analysis.recentPaths')).toContain('C:/data');
});

test('allows user to cancel an ongoing analysis job', async () => {
  jest.useFakeTimers();

  const samplePdfs = [{ id: 1, filename: 'doc.pdf' }];
  mockedAxios.post.mockResolvedValueOnce({ data: samplePdfs });
  mockedAxios.post.mockResolvedValueOnce({ data: { job_id: 'job123', status: 'queued' } });
  mockedAxios.post.mockResolvedValueOnce({
    data: {
      job_id: 'job123',
      status: 'cancelled',
      progress: 0,
      processed_count: 0,
      total_count: 1,
      results: [],
      download_ready: false,
      download_path: null,
      error: null,
      current_file: null,
      messages: ['使用者已中止分析流程'],
    },
  });

  const runningStatus = {
    job_id: 'job123',
    status: 'running' as const,
    progress: 20,
    processed_count: 0,
    total_count: 1,
    results: [] as AnalysisResult[],
    download_ready: false,
    download_path: null,
    error: null,
    current_file: 'doc.pdf',
    messages: [],
  };
  mockedAxios.get.mockResolvedValue({ data: runningStatus });

  renderComponent();

  const pathInput = screen.getByPlaceholderText(/請輸入來源資料夾/);
  fireEvent.change(pathInput, { target: { value: 'C:/data' } });

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '載入 PDF' }));
  });
  await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledTimes(1));

  const analyzeButton = await screen.findByRole('button', { name: '開始分析' });
  await act(async () => {
    fireEvent.click(analyzeButton);
  });

  await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(mockedAxios.get).toHaveBeenCalled());

  await act(async () => {
    jest.advanceTimersByTime(600);
  });

  const cancelButton = await screen.findByRole('button', { name: '終止分析' });
  await act(async () => {
    fireEvent.click(cancelButton);
  });

  await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledTimes(3));
  expect(mockedAxios.post.mock.calls[2][0]).toContain('/api/analyze/stop');
});
