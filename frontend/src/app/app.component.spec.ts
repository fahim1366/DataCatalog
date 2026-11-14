import { TestBed } from '@angular/core/testing';
import { AppComponent } from './app.component';
import { ScrollingModule } from '@angular/cdk/scrolling';
import { provideRouter } from '@angular/router';
import { routes } from './app.routes';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';

describe('AppComponent (Enterprise Navigation Shell)', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AppComponent, ScrollingModule],
      providers: [
        provideRouter(routes),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();
  });

  it('should create the root enterprise catalog application component', () => {
    const fixture = TestBed.createComponent(AppComponent);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('should initialize title and catalog signals with expected enterprise defaults', () => {
    const fixture = TestBed.createComponent(AppComponent);
    const app = fixture.componentInstance;
    expect(app.title()).toBe('Enterprise Data Catalog');
    expect(app.persianTitle()).toBe('سامانه جامع کاتالوگ داده سازمانی');
    expect(app.isSidebarOpen()).toBe(true);
    expect(app.currentVersion()).toBe('1.0.0');
  });

  it('should toggle sidebar state reactively using signals', () => {
    const fixture = TestBed.createComponent(AppComponent);
    const app = fixture.componentInstance;
    expect(app.isSidebarOpen()).toBe(true);
    app.toggleSidebar();
    expect(app.isSidebarOpen()).toBe(false);
    app.toggleSidebar();
    expect(app.isSidebarOpen()).toBe(true);
  });

  it('should render core navigation links in sidebar with Persian localization', () => {
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    const catalogLink = compiled.querySelector('#nav-catalog');
    const connLink = compiled.querySelector('#nav-connections');
    const taxLink = compiled.querySelector('#nav-taxonomy');
    const diffLink = compiled.querySelector('#nav-diff');
    const reportsLink = compiled.querySelector('#nav-reports');

    expect(catalogLink).toBeTruthy();
    expect(catalogLink?.textContent).toContain('کاوشگر کاتالوگ');
    expect(connLink?.textContent).toContain('مدیریت اتصالات');
    expect(taxLink?.textContent).toContain('مدیریت تگ‌ها');
    expect(diffLink?.textContent).toContain('مقایسه نسخه‌ها');
    expect(reportsLink?.textContent).toContain('داشبورد گزارشات');
  });
});
