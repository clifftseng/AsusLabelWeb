import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import axios from 'axios';
import Page1, { AnalysisResult } from './Page1';

jest.mock('axios');

const mockedAxios = axios as jest.Mocked<typeof axios>;

const renderComponent = () => {
  const setAnalysisResults = jest.fn<(results: AnalysisResult[]) => void>();
  const setAnalysisPath = jest.fn<(path: string) => void>();
  const props = {
    setAnalysisResults,
    analysisResults: [] as AnalysisResult[],
    setAnalysisPath,
    analysisPath: '',
  };
  render(<Page1 {...props} />);
  return { setAnalysisResults, setAnalysisPath };
};

afterEach(() => {
  jest.clearAllMocks();
  jest.useRealTimers();
});

test('starts analysis and renders results when job completes', async () => {
  jest.useFakeTimers();

  const samplePdfs = [
    { id: 1, filename: 'label.pdf', is_label: true },
    { id: 2, filename: 'doc.pdf', is_label: false },
  ];

  mockedAxios.post.mockResolvedValueOnce({ data: samplePdfs });
  mockedAxios.post.mockResolvedValueOnce({ data: { job_id: 'job123', status: 'queued' } });

  const runningStatus = {
    job_id: 'job123',
    status: 'running',
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
    messages: [...runningStatus.messages, '分析已完成'],
  };

  mockedAxios.get
    .mockResolvedValueOnce({ data: runningStatus })
    .mockResolvedValueOnce({ data: completedStatus });

  const { setAnalysisResults, setAnalysisPath } = renderComponent();

  const pathInput = screen.getByPlaceholderText(/請輸入來源路徑/i);
  fireEvent.change(pathInput, { target: { value: 'C:/data' } });

  const loadButton = screen.getByRole('button', { name: '載入檔案' });
  fireEvent.click(loadButton);

  await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledTimes(1));
  expect(await screen.findByText('PDF 清單')).toBeInTheDocument();

  const analyzeButton = screen.getByRole('button', { name: '開始分析' });
  fireEvent.click(analyzeButton);

  await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(mockedAxios.get).toHaveBeenCalledTimes(1));

  await act(async () => {
    jest.advanceTimersByTime(600);
  });

  await waitFor(() => expect(mockedAxios.get).toHaveBeenCalledTimes(2));

  await waitFor(() => expect(setAnalysisResults).toHaveBeenCalledWith(completedStatus.results));
  expect(setAnalysisPath).toHaveBeenCalledWith('C:/data');

  expect(screen.getByText('100%')).toBeInTheDocument();
  expect(screen.getByText('doc.pdf 分析完成 (1/2)')).toBeInTheDocument();
  expect(screen.getByText('Model_1')).toBeInTheDocument();
  expect(screen.getByText('Model_2')).toBeInTheDocument();
});

test('allows user to cancel an ongoing analysis job', async () => {
  jest.useFakeTimers();

  const samplePdfs = [{ id: 1, filename: 'doc.pdf', is_label: false }];
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
      messages: ['使用者已取消分析'],
    },
  });

  const runningStatus = {
    job_id: 'job123',
    status: 'running',
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

  const pathInput = screen.getByPlaceholderText(/請輸入來源路徑/i);
  fireEvent.change(pathInput, { target: { value: 'C:/data' } });

  fireEvent.click(screen.getByRole('button', { name: '載入檔案' }));
  await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledTimes(1));

  fireEvent.click(screen.getByRole('button', { name: '開始分析' }));

  await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(mockedAxios.get).toHaveBeenCalled());

  await act(async () => {
    jest.advanceTimersByTime(600);
  });

  const cancelButton = await screen.findByRole('button', { name: '停止分析' });
  fireEvent.click(cancelButton);

  await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledTimes(3));
  expect(mockedAxios.post.mock.calls[2][0]).toContain('/api/analyze/stop');
});
