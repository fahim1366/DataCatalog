import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../../core/auth/auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="login-wrapper">
      <div class="login-card">
        <!-- Brand Header -->
        <div class="brand-header">
          <div class="logo-box">
            <svg class="logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>
              <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path>
              <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path>
            </svg>
          </div>
          <h1 class="system-title">سامانه جامع کاتالوگ داده سازمانی</h1>
          <p class="system-subtitle">ورود به درگاه حاکمیت داده و کاوش متادیتا</p>
        </div>

        <!-- Error Notification -->
        @if (errorMessage()) {
          <div class="alert-error">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="8" x2="12" y2="12"></line>
              <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
            <span>{{ errorMessage() }}</span>
          </div>
        }

        <!-- Manual Login Form -->
        <form (ngSubmit)="onLogin()" class="login-form">
          <div class="form-group">
            <label class="form-label" for="username">نام کاربری یا ایمیل سازمانی</label>
            <input
              id="username"
              type="text"
              class="form-control"
              [(ngModel)]="username"
              name="username"
              placeholder="مثال: admin یا admin@company.internal"
              required
              [disabled]="isLoading()"
            />
          </div>

          <div class="form-group">
            <label class="form-label" for="password">گذرواژه</label>
            <input
              id="password"
              type="password"
              class="form-control"
              [(ngModel)]="password"
              name="password"
              placeholder="••••••••"
              required
              [disabled]="isLoading()"
            />
          </div>

          <button
            type="submit"
            class="btn btn-primary btn-submit"
            [disabled]="isLoading() || !username || !password"
          >
            @if (isLoading()) {
              <span>در حال اعتبارسنجی...</span>
            } @else {
              <span>ورود به سامانه</span>
            }
          </button>
        </form>

        <!-- Divider -->
        <div class="divider">
          <span>یا ورود سریع آزمایشی (Demo Access)</span>
        </div>

        <!-- 3 One-Click Demo Login Buttons -->
        <div class="demo-buttons-grid">
          <button
            type="button"
            class="btn-demo demo-admin"
            (click)="loginWithDemo('admin', 'Admin#2026')"
            [disabled]="isLoading()"
          >
            <div class="demo-role-badge">SuperAdmin</div>
            <div class="demo-title">ورود به عنوان مدیر ارشد</div>
            <div class="demo-desc mono-font">admin / Admin#2026</div>
          </button>

          <button
            type="button"
            class="btn-demo demo-steward"
            (click)="loginWithDemo('steward', 'Steward#2026')"
            [disabled]="isLoading()"
          >
            <div class="demo-role-badge">Data Steward</div>
            <div class="demo-title">ورود به عنوان راهبر داده</div>
            <div class="demo-desc mono-font">steward / Steward#2026</div>
          </button>

          <button
            type="button"
            class="btn-demo demo-analyst"
            (click)="loginWithDemo('analyst', 'Analyst#2026')"
            [disabled]="isLoading()"
          >
            <div class="demo-role-badge">Data Analyst</div>
            <div class="demo-title">ورود به عنوان تحلیلگر داده</div>
            <div class="demo-desc mono-font">analyst / Analyst#2026</div>
          </button>
        </div>

        <!-- Footer -->
        <div class="login-footer">
          <span>Enterprise Data Catalog v1.0 • معماری امنیتی منطبق بر RBAC و رمزنگاری داده در حالت سکون</span>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .login-wrapper {
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
      padding: 24px;
      direction: rtl;
    }

    .login-card {
      background: #ffffff;
      width: 100%;
      max-width: 520px;
      border-radius: var(--radius-lg);
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.4);
      padding: 36px 32px;
      display: flex;
      flex-direction: column;
      border: 1px solid rgba(255, 255, 255, 0.1);
    }

    .brand-header {
      text-align: center;
      margin-bottom: 24px;
    }

    .logo-box {
      width: 56px;
      height: 56px;
      border-radius: 14px;
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #ffffff;
      display: flex;
      align-items: center;
      justify-content: center;
      margin: 0 auto 16px auto;
      box-shadow: 0 10px 15px -3px rgba(37, 99, 235, 0.3);
    }

    .logo-icon {
      width: 32px;
      height: 32px;
    }

    .system-title {
      font-size: 1.28rem;
      font-weight: 800;
      color: #0f172a;
      margin-bottom: 6px;
    }

    .system-subtitle {
      font-size: 0.88rem;
      color: #64748b;
    }

    .alert-error {
      background-color: #fef2f2;
      border: 1px solid #fecaca;
      color: #991b1b;
      padding: 10px 14px;
      border-radius: var(--radius-md);
      font-size: 0.85rem;
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 18px;
    }

    .login-form {
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .btn-submit {
      width: 100%;
      padding: 11px;
      font-size: 0.95rem;
      margin-top: 4px;
    }

    .divider {
      display: flex;
      align-items: center;
      text-align: center;
      margin: 24px 0 18px 0;
      color: #94a3b8;
      font-size: 0.8rem;
    }

    .divider::before, .divider::after {
      content: '';
      flex: 1;
      border-bottom: 1px solid #e2e8f0;
    }

    .divider span {
      padding: 0 12px;
      font-weight: 500;
    }

    .demo-buttons-grid {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .btn-demo {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 16px;
      border-radius: var(--radius-md);
      border: 1px solid #e2e8f0;
      background-color: #f8fafc;
      cursor: pointer;
      transition: all 0.2s ease;
      text-align: right;
    }

    .btn-demo:hover:not(:disabled) {
      border-color: #3b82f6;
      background-color: #eff6ff;
      transform: translateY(-1px);
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }

    .demo-role-badge {
      font-size: 0.72rem;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 9999px;
      letter-spacing: 0.5px;
    }

    .demo-admin .demo-role-badge { background: #fee2e2; color: #991b1b; }
    .demo-steward .demo-role-badge { background: #e0e7ff; color: #3730a3; }
    .demo-analyst .demo-role-badge { background: #dcfce7; color: #166534; }

    .demo-title {
      font-size: 0.88rem;
      font-weight: 600;
      color: #1e293b;
      flex: 1;
      margin: 0 12px;
    }

    .demo-desc {
      font-size: 0.78rem;
      color: #64748b;
    }

    .login-footer {
      text-align: center;
      margin-top: 24px;
      font-size: 0.72rem;
      color: #94a3b8;
      line-height: 1.5;
    }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LoginComponent {
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);

  username = '';
  password = '';
  readonly isLoading = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);

  onLogin(): void {
    if (!this.username || !this.password) return;
    this.executeAuth(this.username, this.password);
  }

  loginWithDemo(u: string, p: string): void {
    this.username = u;
    this.password = p;
    this.executeAuth(u, p);
  }

  private executeAuth(username: string, pass: string): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.authService.login({ username_or_email: username, password: pass }).subscribe({
      next: () => {
        this.isLoading.set(false);
        this.router.navigate(['/catalog']);
      },
      error: (err) => {
        this.isLoading.set(false);
        const msg = err?.error?.detail || 'نام کاربری یا گذرواژه نامعتبر است.';
        this.errorMessage.set(msg);
      },
    });
  }
}
