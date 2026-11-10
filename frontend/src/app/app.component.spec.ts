import { TestBed } from '@angular/core/testing';
import { AppComponent } from './app.component';
import { ScrollingModule } from '@angular/cdk/scrolling';
import { provideRouter } from '@angular/router';

describe('AppComponent (Smoke & Signal Architecture)', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AppComponent, ScrollingModule],
      providers: [provideRouter([])],
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
});

