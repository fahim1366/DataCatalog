import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subject, Subscription, timer } from 'rxjs';
import { switchMap, takeUntil } from 'rxjs/operators';
import {
  SyncJobListResponse,
  SyncJobResponse,
  SyncJobService,
  SyncJobStatus,
} from '../../core/services/sync-job.service';
import { SyncJobDiagnosticModalComponent } from './sync-job-diagnostic-modal.component';

@Component({
  selector: 'app-sync-monitor',
  standalone: true,
  imports: [CommonModule, FormsModule, SyncJobDiagnosticModalComponent],
  templateUrl: './sync-monitor.component.html',
  styleUrls: ['./sync-monitor.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SyncMonitorComponent implements OnInit, OnDestroy {
  private readonly syncJobService = inject(SyncJobService);
  private readonly destroy$ = new Subject<void>();
  private pollingSub: Subscription | null = null;

  readonly jobs = signal<SyncJobResponse[]>([]);
  readonly totalJobs = signal<number>(0);
  readonly isLoading = signal<boolean>(false);
  readonly isPollingActive = signal<boolean>(true);
  readonly selectedStatus = signal<string>('ALL');
  readonly errorMessage = signal<string | null>(null);
  readonly successMessage = signal<string | null>(null);
  readonly retryingJobId = signal<string | null>(null);

  // Diagnostic Modal
  readonly selectedJobIdForDiag = signal<string | null>(null);
  readonly showDiagModal = signal<boolean>(false);

  private readonly persianDateFormatter = new Intl.DateTimeFormat('fa-IR', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

  ngOnInit(): void {
    this.startPolling();
  }

  ngOnDestroy(): void {
    this.stopPolling();
    this.destroy$.next();
    this.destroy$.complete();
  }

  startPolling(): void {
    this.stopPolling();
    this.isPollingActive.set(true);
    this.fetchJobsOnce();

    this.pollingSub = timer(3000, 3000)
      .pipe(
        takeUntil(this.destroy$),
        switchMap(() => {
          const status = this.selectedStatus();
          return this.syncJobService.getSyncJobs(
            status === 'ALL' ? undefined : status,
            undefined,
            undefined,
            50
          );
        })
      )
      .subscribe({
        next: (res: SyncJobListResponse) => {
          this.jobs.set(res.items);
          this.totalJobs.set(res.total);
          this.isLoading.set(false);
        },
        error: (err: unknown) => {
          const msg = err instanceof Error ? err.message : 'خطا در واکشی وضعیت جاب‌های همگام‌سازی';
          this.errorMessage.set(msg);
          this.isLoading.set(false);
        },
      });
  }

  stopPolling(): void {
    if (this.pollingSub) {
      this.pollingSub.unsubscribe();
      this.pollingSub = null;
    }
    this.isPollingActive.set(false);
  }

  togglePolling(): void {
    if (this.isPollingActive()) {
      this.stopPolling();
    } else {
      this.startPolling();
    }
  }

  onStatusFilterChange(status: string): void {
    this.selectedStatus.set(status);
    this.fetchJobsOnce();
  }

  fetchJobsOnce(): void {
    this.isLoading.set(true);
    const status = this.selectedStatus();
    this.syncJobService
      .getSyncJobs(
        status === 'ALL' ? undefined : status,
        undefined,
        undefined,
        50
      )
      .subscribe({
        next: (res: SyncJobListResponse) => {
          this.jobs.set(res.items);
          this.totalJobs.set(res.total);
          this.isLoading.set(false);
        },
        error: (err: unknown) => {
          const msg = err instanceof Error ? err.message : 'خطا در به‌روزرسانی جاب‌ها';
          this.errorMessage.set(msg);
          this.isLoading.set(false);
        },
      });
  }

  onRetryJob(job: SyncJobResponse): void {
    this.retryingJobId.set(job.id);
    this.successMessage.set(null);

    this.syncJobService.retrySyncJob(job.id).subscribe({
      next: (newJob) => {
        this.retryingJobId.set(null);
        this.successMessage.set(`تلاش مجدد برای جاب ${job.id.substring(0, 8)} با موفقیت ثبت شد (شناسه جدید: ${newJob.id.substring(0, 8)})`);
        this.fetchJobsOnce();
        setTimeout(() => this.successMessage.set(null), 5000);
      },
      error: (err: unknown) => {
        this.retryingJobId.set(null);
        const msg = err instanceof Error ? err.message : 'خطا در ثبت تلاش مجدد جاب';
        this.errorMessage.set(msg);
        setTimeout(() => this.errorMessage.set(null), 5000);
      },
    });
  }

  openDiagnostics(jobId: string): void {
    this.selectedJobIdForDiag.set(jobId);
    this.showDiagModal.set(true);
  }

  formatPersianDate(isoDate?: string | null): string {
    if (!isoDate) return '—';
    try {
      return this.persianDateFormatter.format(new Date(isoDate));
    } catch {
      return isoDate;
    }
  }

  getStatusBadgeClass(status: SyncJobStatus): string {
    switch (status) {
      case 'SUCCESS':
        return 'badge-status-success';
      case 'PARTIAL_FAILURE':
        return 'badge-status-partial_failure';
      case 'FAILED':
        return 'badge-status-failed';
      case 'RUNNING':
        return 'badge-status-running';
      case 'QUEUED':
      default:
        return 'badge-status-queued';
    }
  }

  getStatusLabel(status: SyncJobStatus): string {
    switch (status) {
      case 'SUCCESS':
        return 'موفق (SUCCESS)';
      case 'PARTIAL_FAILURE':
        return 'شکست جزئی (PARTIAL_FAILURE)';
      case 'FAILED':
        return 'شکست‌خورده (FAILED)';
      case 'RUNNING':
        return 'در حال اجرا (RUNNING)';
      case 'QUEUED':
        return 'در صف انتظار (QUEUED)';
      default:
        return status;
    }
  }

  getTriggerLabel(trigger: string): string {
    switch (trigger) {
      case 'SCHEDULE':
        return '⏱️ زمان‌بندی خودکار';
      case 'MANUAL':
        return '👤 دستی (Manual)';
      case 'RETRY':
        return '🔄 تکرار مجدد (Retry)';
      default:
        return trigger;
    }
  }
}
