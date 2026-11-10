import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Output,
  computed,
  inject,
  input,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ScrollingModule } from '@angular/cdk/scrolling';
import { CatalogStore } from '../../stores/catalog.store';
import { SearchHit } from '../../models/catalog.models';

@Component({
  selector: 'app-table-virtual-scroll',
  standalone: true,
  imports: [CommonModule, ScrollingModule, RouterLink],
  templateUrl: './table-virtual-scroll.component.html',
  styleUrls: ['./table-virtual-scroll.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TableVirtualScrollComponent {
  readonly store = inject(CatalogStore);

  /**
   * Optional custom items input. Defaults to store.searchHits() if not provided.
   */
  readonly itemsInput = input<SearchHit[] | undefined>(undefined, { alias: 'items' });

  /**
   * Reactive signal resolving items to render.
   */
  readonly items = computed<SearchHit[]>(() => this.itemsInput() ?? this.store.searchHits());

  /**
   * Emitted when user scrolls near the end of the loaded catalog items.
   */
  @Output() readonly nearBottom = new EventEmitter<void>();

  /**
   * Deterministic trackBy function using entity UUID.
   */
  trackById(index: number, item: SearchHit): string {
    return item.id;
  }

  /**
   * Handles scroll position changes from cdk-virtual-scroll-viewport.
   * Automatically triggers keyset pagination when approaching the end of current dataset.
   */
  onScroll(scrolledIndex: number): void {
    const total = this.items().length;
    // Trigger when user is within 15 items of current end
    if (total > 0 && scrolledIndex + 15 >= total) {
      if (this.store.hasMore() && !this.store.isLoading()) {
        this.store.loadNextPage();
        this.nearBottom.emit();
      }
    }
  }
}

