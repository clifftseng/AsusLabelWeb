import asyncio
from dataclasses import dataclass, field
from typing import Callable, List

import pytest

from main import (
    AnalysisJobState,
    AnalysisManager,
    AnalysisPipeline,
    AnalysisResult,
    AnalyzeRequest,
    JobCallbacks,
    PipelineResult,
    PDFFile,
)

pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _CountingPipeline(AnalysisPipeline):
    def __init__(self, tracker: "ConcurrencyTracker", delay: float = 0.05) -> None:
        self.tracker = tracker
        self.delay = delay

    async def run(self, job: AnalysisJobState, callbacks: JobCallbacks) -> PipelineResult:
        await callbacks.set_total(1)
        async with self.tracker.lock:
            self.tracker.running += 1
            self.tracker.max_running = max(self.tracker.max_running, self.tracker.running)

        await asyncio.sleep(self.delay)
        await callbacks.add_result(
            AnalysisResult(
                id=1,
                filename="fake.pdf",
                model_name="MODEL",
                voltage="10V",
                typ_batt_capacity_wh="50Wh",
                typ_capacity_mah="4000mAh",
                rated_capacity_mah="3800mAh",
                rated_energy_wh="48Wh",
            )
        )
        await callbacks.mark_download(None)
        async with self.tracker.lock:
            self.tracker.running -= 1
        return PipelineResult(download_path=None)


@dataclass
class ConcurrencyTracker:
    running: int = 0
    max_running: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _analysis_payload(path: str = ".") -> AnalyzeRequest:
    return AnalyzeRequest(path=path, files=[PDFFile(id=1, filename="fake.pdf", is_label=False)], label_filename=None)


async def _wait_for_all(manager: AnalysisManager, job_ids: List[str]) -> None:
    tasks = [manager.jobs[job_id].task for job_id in job_ids]
    await asyncio.gather(*(task for task in tasks if task is not None))


@pytest.mark.anyio
async def test_analysis_manager_limits_concurrency(tmp_path):
    tracker = ConcurrencyTracker()

    def pipeline_factory() -> AnalysisPipeline:
        return _CountingPipeline(tracker, delay=0.05)

    manager = AnalysisManager(
        pipeline_factory=pipeline_factory,
        max_concurrent_jobs=2,
        base_dir=tmp_path,
    )

    responses = []
    for _ in range(4):
        response = await manager.start_job(_analysis_payload())
        responses.append(response)

    job_ids = [response.job_id for response in responses]
    await _wait_for_all(manager, job_ids)

    assert tracker.max_running <= 2
    statuses = [await manager.get_status(job_id) for job_id in job_ids]
    assert all(status.status == "completed" for status in statuses)


@pytest.mark.anyio
async def test_analysis_manager_creates_job_directory(tmp_path):
    tracker = ConcurrencyTracker()
    manager = AnalysisManager(
        pipeline_factory=lambda: _CountingPipeline(tracker, delay=0.01),
        max_concurrent_jobs=1,
        base_dir=tmp_path,
    )

    response = await manager.start_job(_analysis_payload())
    await _wait_for_all(manager, [response.job_id])

    job = manager.jobs[response.job_id]
    assert job.job_dir.is_dir()
    assert job.job_dir.parent == tmp_path
