import {
  ChangeDetectionStrategy,
  Component,
  OnChanges,
  SimpleChanges,
  inject,
  input,
  model,
  signal,
} from "@angular/core";
import { CommonModule } from "@angular/common";
import {
  SyncJobDetailResponse,
  SyncJobService,
} from "../../core/services/sync-job.service";

@Component({
  selector: "app-sync-job-diagnostic-modal",
  standalone: true,
  imports: [CommonModule],
  templateUrl: "./sync-job-diagnostic-modal.component.html",
  styleUrls: ["./sync-job-diagnostic-modal.component.css"],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SyncJobDiagnosticModalComponent implements OnChanges {
  private readonly syncJobService = inject(SyncJobService);

  readonly jobId = input<string | null>(null);
  readonly visible = model<boolean>(false);

  readonly isLoading = signal<boolean>(false);
  readonly jobDetail = signal<SyncJobDetailResponse | null>(null);
  readonly errorMessage = signal<string | null>(null);
  readonly copied = signal<boolean>(false);

  ngOnChanges(changes: SimpleChanges): void {
    const jobId = this.jobId();
    if (changes["visible"] && this.visible() && jobId) {
      this.loadDetail(jobId);
    }
  }

  loadDetail(id: string): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);
    this.copied.set(false);

    this.syncJobService.getSyncJobDetail(id).subscribe({
      next: (detail) => {
        this.jobDetail.set(detail);
        this.isLoading.set(false);
      },
      error: (err: unknown) => {
        const msg =
          err instanceof Error
            ? err.message
            : "خطا در بارگذاری جزئیات تشخیصی جاب همگام‌سازی";
        this.errorMessage.set(msg);
        this.isLoading.set(false);
      },
    });
  }

  copyErrorTrace(): void {
    const detail = this.jobDetail();
    if (!detail) return;

    const payload = JSON.stringify(detail.error_trace_jsonb || {}, null, 2);
    if (navigator?.clipboard?.writeText) {
      navigator.clipboard.writeText(payload).then(() => {
        this.copied.set(true);
        setTimeout(() => this.copied.set(false), 3000);
      });
    }
  }

  close(): void {
    this.visible.set(false);
  }
}
