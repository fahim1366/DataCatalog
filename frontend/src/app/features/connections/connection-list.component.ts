import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { ConnectionService } from './services/connection.service';
import { ConnectionDialogComponent } from './connection-dialog.component';
import {
  ConnectionCreateRequest,
  ConnectionTestResult,
  DatabaseConnection,
} from './models/connection.models';

@Component({
  selector: 'app-connection-list',
  standalone: true,
  imports: [CommonModule, ConnectionDialogComponent],
  templateUrl: './connection-list.component.html',
  styleUrls: ['./connection-list.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConnectionListComponent implements OnInit {
  private readonly connectionService = inject(ConnectionService);

  readonly connections = signal<DatabaseConnection[]>([]);
  readonly isLoading = signal<boolean>(false);
  readonly showDialog = signal<boolean>(false);
  readonly testingConnectionId = signal<string | null>(null);
  readonly testResults = signal<Record<string, ConnectionTestResult>>({});
  readonly errorMessage = signal<string | null>(null);

  ngOnInit(): void {
    this.loadConnections();
  }

  loadConnections(): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.connectionService.getConnections().subscribe({
      next: (items) => {
        this.connections.set(items);
        this.isLoading.set(false);
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Failed to load database connections';
        this.errorMessage.set(msg);
        this.isLoading.set(false);
      },
    });
  }

  openCreateDialog(): void {
    this.showDialog.set(true);
  }

  closeDialog(): void {
    this.showDialog.set(false);
  }

  onSaveConnection(request: ConnectionCreateRequest): void {
    this.connectionService.createConnection(request).subscribe({
      next: (newConn) => {
        this.connections.update((list) => [newConn, ...list]);
        this.closeDialog();
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Failed to register database connection';
        this.errorMessage.set(msg);
      },
    });
  }

  onTestConnection(id: string): void {
    this.testingConnectionId.set(id);

    this.connectionService.testConnection(id).subscribe({
      next: (result) => {
        this.testResults.update((prev) => ({
          ...prev,
          [id]: result,
        }));
        this.testingConnectionId.set(null);
      },
      error: (err: unknown) => {
        const errorResult: ConnectionTestResult = {
          success: false,
          latency_ms: 5000.0,
          dialect: 'POSTGRESQL',
          message: err instanceof Error ? err.message : 'Connection test failed',
          tested_at: new Date().toISOString(),
        };
        this.testResults.update((prev) => ({
          ...prev,
          [id]: errorResult,
        }));
        this.testingConnectionId.set(null);
      },
    });
  }
}

