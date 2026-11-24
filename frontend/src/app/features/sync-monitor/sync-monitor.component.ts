import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnDestroy,
  OnInit,
  computed,
  inject,
  signal,
} from "@angular/core";

import { FormsModule } from "@angular/forms";
import { Subject, Subscription, timer } from "rxjs";
import { switchMap, takeUntil } from "rxjs/operators";
import {
  SyncJobListResponse,
  SyncJobQueueItem,
  SyncJobResponse,
  SyncJobService,
  SyncJobStatus,
  SyncQueueStatusResponse,
} from "../../core/services/sync-job.service";
import { ConnectionService } from "../connections/services/connection.service";
import { DatabaseConnection } from "../connections/models/connection.models";
import { SyncJobDiagnosticModalComponent } from "./sync-job-diagnostic-modal.component";

@Component({
  selector: "app-sync-monitor",
  standalone: true,
  imports: [FormsModule, SyncJobDiagnosticModalComponent],
  templateUrl: "./sync-monitor.component.html",
  styleUrls: ["./sync-monitor.component.css"],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SyncMonitorComponent implements OnInit, OnDestroy {
  private readonly syncJobService = inject(SyncJobService);
  private readonly connectionService = inject(ConnectionService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly destroy$ = new Subject<void>();
  private pollingSub: Subscription | null = null;

  readonly jobs = signal<SyncJobResponse[]>([]);
  readonly totalJobs = signal<number>(0);
  readonly isLoading = signal<boolean>(false);
  readonly isPollingActive = signal<boolean>(true);
  readonly selectedStatus = signal<string>("ALL");
  readonly errorMessage = signal<string | null>(null);
  readonly successMessage = signal<string | null>(null);
  readonly retryingJobId = signal<string | null>(null);

  // Live Queue & Out-of-order execution states
  readonly queueStatus = signal<SyncQueueStatusResponse | null>(null);
  readonly isExpeditingJobId = signal<string | null>(null);
  readonly isCancellingJobId = signal<string | null>(null);

  // Batch Expedite Modal state
  readonly showBatchModal = signal<boolean>(false);
  readonly availableConnections = signal<DatabaseConnection[]>([]);
  readonly selectedConnectionIds = signal<string[]>([]);
  readonly connectionSearchQuery = signal<string>("");
  readonly isLoadingConnections = signal<boolean>(false);
  readonly isBatchSubmitting = signal<boolean>(false);

  // Diagnostic Modal
  readonly selectedJobIdForDiag = signal<string | null>(null);
  readonly showDiagModal = signal<boolean>(false);

  readonly filteredConnections = computed(() => {
    const q = this.connectionSearchQuery().trim().toLowerCase();
    const conns = this.availableConnections();
    if (!q) return conns;
    return conns.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.database_name.toLowerCase().includes(q) ||
        c.host.toLowerCase().includes(q) ||
        c.engine_type.toLowerCase().includes(q),
    );
  });

  private readonly persianDateFormatter = new Intl.DateTimeFormat("fa-IR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  constructor() {
    this.destroyRef.onDestroy(() => {
      this.stopPolling();
    });
  }

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
    this.fetchQueueOnce();

    this.pollingSub = timer(3000, 3000)
      .pipe(
        takeUntil(this.destroy$),
        switchMap(() => {
          this.fetchQueueOnce();
          const status = this.selectedStatus();
          return this.syncJobService.getSyncJobs(
            status === "ALL" ? undefined : status,
            undefined,
            undefined,
            50,
          );
        }),
      )
      .subscribe({
        next: (res: SyncJobListResponse) => {
          this.jobs.set(res.items);
          this.totalJobs.set(res.total);
          this.isLoading.set(false);
        },
        error: (err: unknown) => {
          const msg =
            err instanceof Error
              ? err.message
              : "خطا در واکشی وضعیت جاب‌های همگام‌سازی";
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

  fetchQueueOnce(): void {
    if (!this.syncJobService.getQueueStatus) return;
    this.syncJobService.getQueueStatus().subscribe({
      next: (q) => {
        this.queueStatus.set(q);
      },
      error: () => {},
    });
  }

  fetchJobsOnce(): void {
    this.isLoading.set(true);
    this.fetchQueueOnce();
    const status = this.selectedStatus();
    this.syncJobService
      .getSyncJobs(
        status === "ALL" ? undefined : status,
        undefined,
        undefined,
        50,
      )
      .subscribe({
        next: (res: SyncJobListResponse) => {
          this.jobs.set(res.items);
          this.totalJobs.set(res.total);
          this.isLoading.set(false);
        },
        error: (err: unknown) => {
          const msg =
            err instanceof Error ? err.message : "خطا در به‌روزرسانی جاب‌ها";
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
        this.successMessage.set(
          `تلاش مجدد برای جاب ${job.id.substring(0, 8)} با موفقیت ثبت شد (شناسه جدید: ${newJob.id.substring(0, 8)})`,
        );
        this.fetchJobsOnce();
        setTimeout(() => this.successMessage.set(null), 5000);
      },
      error: (err: unknown) => {
        this.retryingJobId.set(null);
        const msg =
          err instanceof Error ? err.message : "خطا در ثبت تلاش مجدد جاب";
        this.errorMessage.set(msg);
        setTimeout(() => this.errorMessage.set(null), 5000);
      },
    });
  }

  onExpediteJob(job: SyncJobResponse | SyncJobQueueItem): void {
    this.isExpeditingJobId.set(job.id);
    this.errorMessage.set(null);
    this.successMessage.set(null);

    this.syncJobService.expediteSyncJob(job.id).subscribe({
      next: () => {
        this.isExpeditingJobId.set(null);
        this.successMessage.set(
          `جاب ${job.id.substring(0, 8)} با موفقیت و بالاترین اولویت به اول صف منتقل و اجرا شد.`,
        );
        this.fetchJobsOnce();
        setTimeout(() => this.successMessage.set(null), 5000);
      },
      error: (err: unknown) => {
        this.isExpeditingJobId.set(null);
        const msg =
          err instanceof Error ? err.message : "خطا در اجرای فوری جاب";
        this.errorMessage.set(msg);
        setTimeout(() => this.errorMessage.set(null), 5000);
      },
    });
  }

  onCancelJob(job: SyncJobResponse | SyncJobQueueItem): void {
    this.isCancellingJobId.set(job.id);
    this.errorMessage.set(null);
    this.successMessage.set(null);

    this.syncJobService.cancelSyncJob(job.id).subscribe({
      next: () => {
        this.isCancellingJobId.set(null);
        this.successMessage.set(
          `جاب ${job.id.substring(0, 8)} با موفقیت لغو شد و قفل همگام‌سازی آزاد گردید.`,
        );
        this.fetchJobsOnce();
        setTimeout(() => this.successMessage.set(null), 5000);
      },
      error: (err: unknown) => {
        this.isCancellingJobId.set(null);
        const msg =
          err instanceof Error ? err.message : "خطا در لغو جاب همگام‌سازی";
        this.errorMessage.set(msg);
        setTimeout(() => this.errorMessage.set(null), 5000);
      },
    });
  }

  openBatchExpediteModal(): void {
    this.showBatchModal.set(true);
    this.selectedConnectionIds.set([]);
    this.connectionSearchQuery.set("");
    this.loadAvailableConnections();
  }

  closeBatchExpediteModal(): void {
    this.showBatchModal.set(false);
  }

  loadAvailableConnections(): void {
    this.isLoadingConnections.set(true);
    this.connectionService.getConnections(true).subscribe({
      next: (conns) => {
        this.availableConnections.set(conns || []);
        this.isLoadingConnections.set(false);
      },
      error: () => {
        this.isLoadingConnections.set(false);
      },
    });
  }

  isConnectionSelected(id: string): boolean {
    return this.selectedConnectionIds().includes(id);
  }

  toggleConnectionSelection(id: string): void {
    const current = this.selectedConnectionIds();
    if (current.includes(id)) {
      this.selectedConnectionIds.set(current.filter((item) => item !== id));
    } else {
      this.selectedConnectionIds.set([...current, id]);
    }
  }

  areAllFilteredSelected(): boolean {
    const filtered = this.filteredConnections();
    if (filtered.length === 0) return false;
    const current = this.selectedConnectionIds();
    return filtered.every((c) => current.includes(c.id));
  }

  toggleSelectAllConnections(): void {
    const filtered = this.filteredConnections();
    if (this.areAllFilteredSelected()) {
      const filteredIds = new Set(filtered.map((c) => c.id));
      this.selectedConnectionIds.set(
        this.selectedConnectionIds().filter((id) => !filteredIds.has(id)),
      );
    } else {
      const combined = new Set([
        ...this.selectedConnectionIds(),
        ...filtered.map((c) => c.id),
      ]);
      this.selectedConnectionIds.set(Array.from(combined));
    }
  }

  onBatchExpediteSubmit(): void {
    const ids = this.selectedConnectionIds();
    if (ids.length === 0) return;

    this.isBatchSubmitting.set(true);
    this.errorMessage.set(null);
    this.successMessage.set(null);

    this.syncJobService.batchExpediteSync(ids).subscribe({
      next: (res) => {
        this.isBatchSubmitting.set(false);
        this.showBatchModal.set(false);
        this.successMessage.set(res.message);
        this.fetchJobsOnce();
        setTimeout(() => this.successMessage.set(null), 6000);
      },
      error: (err: unknown) => {
        this.isBatchSubmitting.set(false);
        const msg =
          err instanceof Error
            ? err.message
            : "خطا در ارسال همگام‌سازی دسته‌ای خارج از نوبت";
        this.errorMessage.set(msg);
        setTimeout(() => this.errorMessage.set(null), 6000);
      },
    });
  }

  openDiagnostics(jobId: string): void {
    this.selectedJobIdForDiag.set(jobId);
    this.showDiagModal.set(true);
  }

  formatPersianDate(isoDate?: string | null): string {
    if (!isoDate) return "—";
    try {
      return this.persianDateFormatter.format(new Date(isoDate));
    } catch {
      return isoDate;
    }
  }

  getStatusBadgeClass(status: SyncJobStatus): string {
    switch (status) {
      case "SUCCESS":
        return "badge-status-success";
      case "PARTIAL_FAILURE":
        return "badge-status-partial_failure";
      case "FAILED":
        return "badge-status-failed";
      case "RUNNING":
        return "badge-status-running";
      case "CANCELLED":
        return "badge-status-cancelled";
      case "QUEUED":
      default:
        return "badge-status-queued";
    }
  }

  getStatusLabel(status: SyncJobStatus): string {
    switch (status) {
      case "SUCCESS":
        return "موفق (SUCCESS)";
      case "PARTIAL_FAILURE":
        return "شکست جزئی (PARTIAL_FAILURE)";
      case "FAILED":
        return "شکست‌خورده (FAILED)";
      case "RUNNING":
        return "در حال اجرا (RUNNING)";
      case "CANCELLED":
        return "لغو شده (CANCELLED)";
      case "QUEUED":
        return "در صف انتظار (QUEUED)";
      default:
        return status;
    }
  }

  getTriggerLabel(trigger: string): string {
    switch (trigger) {
      case "SCHEDULE":
        return "⏱️ زمان‌بندی خودکار";
      case "MANUAL":
        return "👤 دستی (Manual)";
      case "RETRY":
        return "🔄 تکرار مجدد (Retry)";
      default:
        return trigger;
    }
  }
}

