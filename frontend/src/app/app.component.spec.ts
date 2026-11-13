import { TestBed } from '@angular/core/testing';
import { AppComponent } from './app.component';
import { ScrollingModule } from '@angular/cdk/scrolling';
import { provideRouter } from '@angular/router';
import { By } from '@angular/platform-browser';
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

  it('should render all 5 core navigation links in sidebar', () => {
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    const navLinks = compiled.querySelectorAll('.nav-menu a.nav-item');
    expect(navLinks.length).toBe(5);

    const catalogLink = compiled.querySelector('#nav-catalog');
    const connLink = compiled.querySelector('#nav-connections');
    const diffLink = compiled.querySelector('#nav-diff');
    const auditLink = compiled.querySelector('#nav-audit');
    const reportsLink = compiled.querySelector('#nav-reports');

    expect(catalogLink?.textContent?.trim()).toBe('Catalog Search');
    expect(connLink?.textContent?.trim()).toBe('Connections');
    expect(diffLink?.textContent?.trim()).toBe('Version Diff Viewer');
    expect(auditLink?.textContent?.trim()).toBe('Audit Log Viewer');
    expect(reportsLink?.textContent?.trim()).toBe('Executive Dashboard');
  });
});
