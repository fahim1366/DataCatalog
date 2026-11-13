import { Component, ChangeDetectionStrategy, signal } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { ScrollingModule } from '@angular/cdk/scrolling';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ScrollingModule],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent {
  readonly title = signal<string>('Enterprise Data Catalog');
  readonly isSidebarOpen = signal<boolean>(true);
  readonly currentVersion = signal<string>('1.0.0');

  toggleSidebar(): void {
    this.isSidebarOpen.update((open) => !open);
  }
}

// Alias for compatibility
export { AppComponent as App };
