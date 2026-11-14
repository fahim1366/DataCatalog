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
import { HasPermissionDirective } from '../../core/directives/has-permission.directive';
import {
  ConnectionCreateRequest,
  ConnectionTestResult,
  DatabaseConnection,
} from './models/connection.models';

@Component({
  selector: 'app-connection-list',
  standalone: true,
  imports: [CommonModule, ConnectionDialogComponent, HasPermissionDirective],
  templateUrl: './connection-list.component.html',
  styleUrls: ['./connection-list.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConnectionListComponent implements OnInit {
  private readonly connectionService = inject(ConnectionService);

  readonly connections = signal<DatabaseConnection[]>([]);
  readonly isLoading = signal<boolean>(false);
  readonly showDialog = signal<boolean>(false);
  readonly connectionToEdit = signal<DatabaseConnection | null>(null);
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
        const msg = err instanceof Error ? err.message : 'خطا در بارگذاری لیست اتصالات پایگاه داده';
        this.errorMessage.set(msg);
        this.isLoading.set(false);
      },
    });
  }

  openCreateDialog(): void {
    this.connectionToEdit.set(null);
    this.showDialog.set(true);
  }

  openEditDialog(conn: DatabaseConnection): void {
    this.connectionToEdit.set(conn);
    this.showDialog.set(true);
  }

  closeDialog(): void {
    this.showDialog.set(false);
    this.connectionToEdit.set(null);
  }

  onSaveConnection(request: ConnectionCreateRequest): void {
    const editConn = this.connectionToEdit();
    if (editConn) {
      this.connectionService.updateConnection(editConn.id, request).subscribe({
        next: (updatedConn) => {
          this.connections.update((list) =>
            list.map((c) => (c.id === updatedConn.id ? updatedConn : c))
          );
          this.closeDialog();
        },
        error: (err: unknown) => {
          const msg = err instanceof Error ? err.message : 'خطا در بروزرسانی اتصال دیتابیس';
          this.errorMessage.set(msg);
        },
      });
    } else {
      this.connectionService.createConnection(request).subscribe({
        next: (newConn) => {
          this.connections.update((list) => [newConn, ...list]);
          this.closeDialog();
        },
        error: (err: unknown) => {
          const msg = err instanceof Error ? err.message : 'خطا در ثبت اتصال جدید پایگاه داده';
          this.errorMessage.set(msg);
        },
      });
    }
  }

  onDeleteConnection(id: string): void {
    if (!confirm('آیا از حذف این اتصال پایگاه‌داده و زمان‌بندی همگام‌سازی آن اطمینان دارید؟')) {
      return;
    }

    this.connectionService.deleteConnection(id).subscribe({
      next: () => {
        this.connections.update((list) => list.filter((c) => c.id !== id));
      },
      error: (err: unknown) => {
        const msg = err instanceof Error ? err.message : 'خطا در حذف اتصال دیتابیس';
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
