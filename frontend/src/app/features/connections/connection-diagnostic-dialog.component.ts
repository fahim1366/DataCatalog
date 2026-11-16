import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { DatabaseConnection } from './models/connection.models';
import {
  CircuitState,
  ConnectionDiagnosticTestResponse,
  SyncJobService,
} from '../../core/services/sync-job.service';

@Component({
  selector: 'app-connection-diagnostic-dialog',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './connection-diagnostic-dialog.component.html',
  styleUrls: ['./connection-diagnostic-dialog.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConnectionDiagnosticDialogComponent implements OnChanges {
  private readonly syncJobService = inject(SyncJobService);

  @Input() connection: DatabaseConnection | null = null;
  @Input() visible = false;
  @Output() visibleChange = new EventEmitter<boolean>();
  @Output() circuitBreakerReset = new EventEmitter<void>();

  readonly isLoading = signal<boolean>(false);
  readonly isResetting = signal<boolean>(false);
  readonly testResult = signal<ConnectionDiagnosticTestResponse | null>(null);
  readonly errorMessage = signal<string | null>(null);
  readonly circuitState = signal<CircuitState>('CLOSED');
  readonly consecutiveFailures = signal<number>(0);
  readonly cooldownUntil = signal<string | null>(null);

  ngOnChanges(changes: SimpleChanges): void {
    if (this.connection) {
      if (this.connection.sync_schedule) {
        this.circuitState.set(
          (this.connection.sync_schedule.circuit_state as CircuitState) ?? 'CLOSED'
        );
        this.consecutiveFailures.set(this.connection.sync_schedule.consecutive_failures ?? 0);
        this.cooldownUntil.set(this.connection.sync_schedule.cooldown_until ?? null);
      } else {
        this.circuitState.set('CLOSED');
        this.consecutiveFailures.set(0);
        this.cooldownUntil.set(null);
      }
    }
    if (changes['visible'] && this.visible && this.connection) {
      this.runDiagnostic();
    }
  }

  runDiagnostic(): void {
    if (!this.connection) return;

    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.syncJobService.testConnection(this.connection.id).subscribe({
      next: (res) => {
        this.testResult.set(res);
        this.isLoading.set(false);
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'خطا در برقراری ارتباط با پایگاه داده';
        this.errorMessage.set(msg);
        this.isLoading.set(false);
      },
    });
  }

  resetCircuitBreakerAction(): void {
    if (!this.connection) return;

    this.isResetting.set(true);
    this.syncJobService.resetCircuitBreaker(this.connection.id).subscribe({
      next: (res) => {
        this.circuitState.set(res.circuit_state);
        this.consecutiveFailures.set(res.consecutive_failures);
        this.cooldownUntil.set(res.cooldown_until || null);
        this.isResetting.set(false);
        this.circuitBreakerReset.emit();
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'خطا در بازنشانی مدارشکن';
        this.errorMessage.set(msg);
        this.isResetting.set(false);
      },
    });
  }

  close(): void {
    this.visible = false;
    this.visibleChange.emit(false);
  }
}
