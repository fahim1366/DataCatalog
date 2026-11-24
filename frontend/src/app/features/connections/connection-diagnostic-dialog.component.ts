import {
  ChangeDetectionStrategy,
  Component,
  OnChanges,
  SimpleChanges,
  inject,
  input,
  model,
  output,
  signal,
} from "@angular/core";

import { DatabaseConnection } from "./models/connection.models";
import {
  CircuitState,
  ConnectionDiagnosticTestResponse,
  SyncJobService,
} from "../../core/services/sync-job.service";

@Component({
  selector: "app-connection-diagnostic-dialog",
  standalone: true,
  imports: [],
  templateUrl: "./connection-diagnostic-dialog.component.html",
  styleUrls: ["./connection-diagnostic-dialog.component.css"],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConnectionDiagnosticDialogComponent implements OnChanges {
  private readonly syncJobService = inject(SyncJobService);

  readonly connection = input<DatabaseConnection | null>(null);
  readonly visible = model<boolean>(false);
  readonly circuitBreakerReset = output<void>();

  readonly isLoading = signal<boolean>(false);
  readonly isResetting = signal<boolean>(false);
  readonly testResult = signal<ConnectionDiagnosticTestResponse | null>(null);
  readonly errorMessage = signal<string | null>(null);
  readonly circuitState = signal<CircuitState>("CLOSED");
  readonly consecutiveFailures = signal<number>(0);
  readonly cooldownUntil = signal<string | null>(null);

  ngOnChanges(changes: SimpleChanges): void {
    const connection = this.connection();
    if (connection) {
      if (connection.sync_schedule) {
        this.circuitState.set(
          (connection.sync_schedule.circuit_state as CircuitState) ?? "CLOSED",
        );
        this.consecutiveFailures.set(
          connection.sync_schedule.consecutive_failures ?? 0,
        );
        this.cooldownUntil.set(connection.sync_schedule.cooldown_until ?? null);
      } else {
        this.circuitState.set("CLOSED");
        this.consecutiveFailures.set(0);
        this.cooldownUntil.set(null);
      }
    }
    if (
      (changes["visible"] || changes["connection"]) &&
      this.visible() &&
      connection
    ) {
      this.runDiagnostic();
    }
  }

  runDiagnostic(): void {
    const connection = this.connection();
    if (!connection) return;

    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.syncJobService.testConnection(connection.id).subscribe({
      next: (res) => {
        this.testResult.set(res);
        this.isLoading.set(false);
      },
      error: (err: unknown) => {
        const msg =
          err instanceof Error
            ? err.message
            : "خطا در برقراری ارتباط با پایگاه داده";
        this.errorMessage.set(msg);
        this.isLoading.set(false);
      },
    });
  }

  resetCircuitBreakerAction(): void {
    const connection = this.connection();
    if (!connection) return;

    this.isResetting.set(true);
    this.syncJobService.resetCircuitBreaker(connection.id).subscribe({
      next: (res) => {
        this.circuitState.set(res.circuit_state);
        this.consecutiveFailures.set(res.consecutive_failures);
        this.cooldownUntil.set(res.cooldown_until || null);
        this.isResetting.set(false);
        this.circuitBreakerReset.emit();
      },
      error: (err: unknown) => {
        const msg =
          err instanceof Error ? err.message : "خطا در بازنشانی مدارشکن";
        this.errorMessage.set(msg);
        this.isResetting.set(false);
      },
    });
  }

  close(): void {
    this.visible.set(false);
  }
}
