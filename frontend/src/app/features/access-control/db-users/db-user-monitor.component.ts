import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { ConnectionService } from '../../connections/services/connection.service';
import { DatabaseConnection } from '../../connections/models/connection.models';
import { DbUserService } from '../services/db-user.service';
import { DatabaseUserItem } from '../models/db-user.models';
import { HasPermissionDirective } from '../../../core/directives/has-permission.directive';

export type UserFilterType = 'ALL' | 'LOGIN' | 'ROLES' | 'DISABLED';

@Component({
  selector: 'app-db-user-monitor',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule, HasPermissionDirective],
  templateUrl: './db-user-monitor.component.html',
  styleUrls: ['./db-user-monitor.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DbUserMonitorComponent implements OnInit {
  private readonly connectionService = inject(ConnectionService);
  private readonly dbUserService = inject(DbUserService);
  private readonly route = inject(ActivatedRoute);

  // Notifications
  successMessage = signal<string | null>(null);
  errorMessage = signal<string | null>(null);

  // Connections list state
  connections = signal<DatabaseConnection[]>([]);
  isLoadingConnections = signal<boolean>(false);
  connectionSearchQuery = signal<string>('');
  selectedConnection = signal<DatabaseConnection | null>(null);

  // Users list state
  users = signal<DatabaseUserItem[]>([]);
  isLoadingUsers = signal<boolean>(false);
  userSearchQuery = signal<string>('');
  userTypeFilter = signal<UserFilterType>('ALL');

  // Filtered connections list
  filteredConnections = computed<DatabaseConnection[]>(() => {
    const q = this.connectionSearchQuery().trim().toLowerCase();
    const conns = this.connections();
    if (!q) return conns;
    return conns.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.host.toLowerCase().includes(q) ||
        c.database_name.toLowerCase().includes(q) ||
        c.engine_type.toLowerCase().includes(q)
    );
  });

  // Filtered users list
  filteredUsers = computed<DatabaseUserItem[]>(() => {
    const query = this.userSearchQuery().trim().toLowerCase();
    const filter = this.userTypeFilter();
    let list = this.users();

    if (filter === 'LOGIN') {
      list = list.filter((u) => u.can_login);
    } else if (filter === 'ROLES') {
      list = list.filter((u) => !u.can_login);
    } else if (filter === 'DISABLED') {
      list = list.filter((u) => !u.can_login);
    }

    if (query) {
      list = list.filter(
        (u) =>
          u.username.toLowerCase().includes(query) ||
          u.member_of_roles.some((r) => r.toLowerCase().includes(query))
      );
    }

    return list;
  });

  // Counts for summary pills
  userStats = computed(() => {
    const all = this.users();
    return {
      total: all.length,
      loginUsers: all.filter((u) => u.can_login).length,
      roles: all.filter((u) => !u.can_login).length,
      activeSessions: all.reduce((acc, u) => acc + (u.active_sessions || 0), 0),
    };
  });

  // Confirmation modal state for Login Toggle
  toggleLoginTarget = signal<DatabaseUserItem | null>(null);
  isTogglingLogin = signal<boolean>(false);

  // Edit settings modal state
  editTarget = signal<DatabaseUserItem | null>(null);
  editConnectionLimit = signal<number>(-1);
  editValidUntil = signal<string>('');
  editIsUnlimited = signal<boolean>(true);
  isSavingSettings = signal<boolean>(false);

  ngOnInit(): void {
    this.loadConnections();
  }

  loadConnections(): void {
    this.isLoadingConnections.set(true);
    this.connectionService.getConnections().subscribe({
      next: (conns) => {
        this.connections.set(conns);
        this.isLoadingConnections.set(false);

        // Check if query param specifies connectionId
        const targetId = this.route.snapshot.queryParams['connectionId'];
        if (targetId) {
          const match = conns.find((c) => c.id === targetId);
          if (match) {
            this.selectConnection(match);
            return;
          }
        }

        // Auto-select first PostgreSQL connection or first connection
        if (conns.length > 0) {
          const firstPg = conns.find((c) => c.engine_type === 'POSTGRESQL') || conns[0];
          this.selectConnection(firstPg);
        }
      },
      error: (err) => {
        this.isLoadingConnections.set(false);
        this.showError('خطا در دریافت لیست اتصالات پایگاه‌های داده: ' + (err.error?.detail || err.message));
      },
    });
  }

  selectConnection(conn: DatabaseConnection): void {
    this.selectedConnection.set(conn);
    this.loadUsers(conn.id);
  }

  loadUsers(connectionId: string): void {
    this.isLoadingUsers.set(true);
    this.users.set([]);
    this.errorMessage.set(null);

    this.dbUserService.getDatabaseUsers(connectionId).subscribe({
      next: (dbUsers) => {
        this.users.set(dbUsers);
        this.isLoadingUsers.set(false);
      },
      error: (err) => {
        this.isLoadingUsers.set(false);
        this.showError('خطا در دریافت لیست کاربران پایگاه داده: ' + (err.error?.detail || err.message));
      },
    });
  }

  refreshCurrentUsers(): void {
    const conn = this.selectedConnection();
    if (conn) {
      this.loadUsers(conn.id);
    }
  }

  // --- Toggle Login Modal ---
  openToggleLoginModal(user: DatabaseUserItem): void {
    this.toggleLoginTarget.set(user);
  }

  closeToggleLoginModal(): void {
    this.toggleLoginTarget.set(null);
  }

  confirmToggleLogin(): void {
    const user = this.toggleLoginTarget();
    const conn = this.selectedConnection();
    if (!user || !conn) return;

    const newLoginState = !user.can_login;
    this.isTogglingLogin.set(true);

    this.dbUserService
      .updateDatabaseUser(conn.id, user.username, { can_login: newLoginState })
      .subscribe({
        next: (res) => {
          this.isTogglingLogin.set(false);
          this.closeToggleLoginModal();
          this.showSuccess(
            `دسترسی ورود کاربر «${user.username}» با موفقیت ${newLoginState ? 'فعال' : 'غیرفعال'} شد.`
          );
          this.refreshCurrentUsers();
        },
        error: (err) => {
          this.isTogglingLogin.set(false);
          this.showError(err.error?.detail || 'خطا در تغییر وضعیت دسترسی ورود کاربر.');
        },
      });
  }

  // --- Edit Settings Modal ---
  openEditModal(user: DatabaseUserItem): void {
    this.editTarget.set(user);
    this.editConnectionLimit.set(user.connection_limit ?? -1);
    if (user.valid_until && user.valid_until.toLowerCase() !== 'infinity') {
      this.editIsUnlimited.set(false);
      // Format to YYYY-MM-DD
      const datePart = user.valid_until.split('T')[0].split(' ')[0];
      this.editValidUntil.set(datePart);
    } else {
      this.editIsUnlimited.set(true);
      this.editValidUntil.set('');
    }
  }

  closeEditModal(): void {
    this.editTarget.set(null);
  }

  saveSettings(): void {
    const user = this.editTarget();
    const conn = this.selectedConnection();
    if (!user || !conn) return;

    this.isSavingSettings.set(true);
    const validUntilValue = this.editIsUnlimited() ? 'infinity' : this.editValidUntil();

    this.dbUserService
      .updateDatabaseUser(conn.id, user.username, {
        connection_limit: this.editConnectionLimit(),
        valid_until: validUntilValue,
      })
      .subscribe({
        next: (res) => {
          this.isSavingSettings.set(false);
          this.closeEditModal();
          this.showSuccess(`تنظیمات کاربر «${user.username}» با موفقیت به‌روزرسانی شد.`);
          this.refreshCurrentUsers();
        },
        error: (err) => {
          this.isSavingSettings.set(false);
          this.showError(err.error?.detail || 'خطا در ذخیره تنظیمات کاربر.');
        },
      });
  }

  formatDate(dateStr: string | null): string {
    if (!dateStr || dateStr.toLowerCase() === 'infinity') {
      return 'نامحدود (دائمی)';
    }
    try {
      const d = new Date(dateStr);
      if (isNaN(d.getTime())) return dateStr;
      return new Intl.DateTimeFormat('fa-IR', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      }).format(d);
    } catch {
      return dateStr;
    }
  }

  private showSuccess(msg: string): void {
    this.successMessage.set(msg);
    setTimeout(() => this.successMessage.set(null), 5000);
  }

  private showError(msg: string): void {
    this.errorMessage.set(msg);
    setTimeout(() => this.errorMessage.set(null), 8000);
  }
}
