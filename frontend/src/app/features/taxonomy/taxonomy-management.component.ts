import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  FormControl,
  FormGroup,
  FormsModule,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { TreeTableModule } from 'primeng/treetable';
import { TreeNode } from 'primeng/api';

import { TagChipComponent } from '../catalog/components/tag-chip/tag-chip.component';
import { TagItem, CategoryTreeNode } from '../catalog/models/catalog.models';
import {
  CategoryService,
  AvailableTableItem,
  CategoryAssignmentItem,
} from './services/category.service';

export interface CategoryRowData {
  id: string;
  name: string;
  description?: string | null;
  parent_id?: string | null;
  status: 'ACTIVE' | 'INACTIVE';
  assigned_tables_count: number;
  tables?: any[];
  databases?: any[];
}

@Component({
  selector: 'app-taxonomy-management',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ReactiveFormsModule,
    TreeTableModule,
    TagChipComponent,
  ],
  templateUrl: './taxonomy-management.component.html',
  styleUrls: ['./taxonomy-management.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TaxonomyManagementComponent implements OnInit {
  private http = inject(HttpClient);
  private categoryService = inject(CategoryService);

  // Active Tab
  activeTab = signal<'categories' | 'tags'>('categories');

  // Loading States
  isLoadingTree = signal<boolean>(false);
  isLoadingTags = signal<boolean>(false);
  isSavingCategory = signal<boolean>(false);
  isLoadingAvailableTables = signal<boolean>(false);

  // Raw Categories & TreeNodes
  rawCategoryTree = signal<CategoryTreeNode[]>([]);
  expandedNodeKeys = signal<Set<string>>(new Set<string>());

  // Filter and Search
  searchTerm = signal<string>('');
  statusFilter = signal<'ALL' | 'ACTIVE' | 'INACTIVE'>('ALL');

  // Mapped PrimeNG TreeNodes
  treeNodes = computed<TreeNode<CategoryRowData>[]>(() => {
    const raw = this.rawCategoryTree();
    const expanded = this.expandedNodeKeys();
    return this.buildTreeNodes(raw, expanded);
  });

  // Filtered TreeNodes
  filteredTreeNodes = computed<TreeNode<CategoryRowData>[]>(() => {
    const nodes = this.treeNodes();
    const term = this.searchTerm().trim().toLowerCase();
    const status = this.statusFilter();

    if (!term && status === 'ALL') {
      return nodes;
    }

    return this.filterTreeRecursively(nodes, term, status);
  });

  // Category Statistics
  totalCategoriesCount = computed<number>(() => {
    return this.countAllCategories(this.rawCategoryTree());
  });

  totalAssignedTablesCount = computed<number>(() => {
    return this.countAllAssignedTables(this.rawCategoryTree());
  });

  // Category Modal State
  isCategoryModalOpen = signal<boolean>(false);
  categoryModalMode = signal<'create-root' | 'create-child' | 'edit'>('create-root');
  selectedParentCategory = signal<CategoryRowData | null>(null);
  editingCategory = signal<CategoryRowData | null>(null);

  // Category Reactive Form
  categoryForm = new FormGroup({
    name: new FormControl<string>('', {
      nonNullable: true,
      validators: [Validators.required, Validators.minLength(1), Validators.maxLength(200)],
    }),
    description: new FormControl<string>(''),
    status: new FormControl<'ACTIVE' | 'INACTIVE'>('ACTIVE', {
      nonNullable: true,
      validators: [Validators.required],
    }),
  });

  // Delete Category Confirmation Modal State
  isDeleteModalOpen = signal<boolean>(false);
  categoryToDelete = signal<CategoryRowData | null>(null);
  deleteSubcategoryCount = signal<number>(0);
  deleteTableCount = signal<number>(0);

  // Assign Tables Modal State
  isAssignModalOpen = signal<boolean>(false);
  targetCategoryForAssign = signal<CategoryRowData | null>(null);
  currentAssignedTables = signal<CategoryAssignmentItem[]>([]);
  availableCatalogTables = signal<AvailableTableItem[]>([]);
  tableSearchQuery = signal<string>('');

  // Governed Tags State
  tags = signal<TagItem[]>([]);
  isTagModalOpen = signal<boolean>(false);
  editingTagId = signal<string | null>(null);
  tagFormName = '';
  tagFormColor = '#3b82f6';
  tagFormDescription = '';
  colorPresets = [
    '#ef4444',
    '#f97316',
    '#f59e0b',
    '#10b981',
    '#06b6d4',
    '#3b82f6',
    '#6366f1',
    '#8b5cf6',
    '#ec4899',
    '#64748b',
  ];

  // Toast Notifications
  toastMessage = signal<string | null>(null);
  toastType = signal<'success' | 'error'>('success');
  private toastTimer: any = null;

  ngOnInit(): void {
    this.loadCategoryTree();
    this.loadTags();
  }

  // ==========================================
  // CATEGORY TREE LOADING & MAPPING
  // ==========================================

  loadCategoryTree(expandTargetId?: string): void {
    this.isLoadingTree.set(true);
    this.categoryService.getCategoryTree().subscribe({
      next: (tree) => {
        this.rawCategoryTree.set(tree);
        if (expandTargetId) {
          this.expandNodeAndAncestors(tree, expandTargetId);
        }
        this.isLoadingTree.set(false);
      },
      error: (err) => {
        this.isLoadingTree.set(false);
        this.showToast(this.extractErrorMessage(err, 'خطا در بارگذاری درخت دسته‌بندی‌ها'), 'error');
      },
    });
  }

  private buildTreeNodes(
    nodes: CategoryTreeNode[],
    expandedKeys: Set<string>
  ): TreeNode<CategoryRowData>[] {
    return nodes.map((node) => {
      const isExpanded = expandedKeys.has(node.id);
      const rowData: CategoryRowData = {
        id: node.id,
        name: node.name,
        description: node.description,
        parent_id: node.parent_id,
        status: (node.status as 'ACTIVE' | 'INACTIVE') || 'ACTIVE',
        assigned_tables_count:
          node.assigned_tables_count !== undefined
            ? node.assigned_tables_count
            : node.tables
              ? node.tables.length
              : 0,
        tables: node.tables || [],
        databases: node.databases || [],
      };

      const children = node.children && node.children.length > 0
        ? this.buildTreeNodes(node.children, expandedKeys)
        : [];

      return {
        key: node.id,
        label: node.name,
        data: rowData,
        expanded: isExpanded,
        children: children,
        leaf: children.length === 0,
      };
    });
  }

  private filterTreeRecursively(
    nodes: TreeNode<CategoryRowData>[],
    term: string,
    status: 'ALL' | 'ACTIVE' | 'INACTIVE'
  ): TreeNode<CategoryRowData>[] {
    const result: TreeNode<CategoryRowData>[] = [];

    for (const node of nodes) {
      const data = node.data!;
      const matchesTerm = !term || data.name.toLowerCase().includes(term) || (data.description && data.description.toLowerCase().includes(term));
      const matchesStatus = status === 'ALL' || data.status === status;

      const filteredChildren = node.children && node.children.length > 0
        ? this.filterTreeRecursively(node.children, term, status)
        : [];

      if ((matchesTerm && matchesStatus) || filteredChildren.length > 0) {
        result.push({
          ...node,
          expanded: term ? true : node.expanded,
          children: filteredChildren,
        });
      }
    }

    return result;
  }

  private expandNodeAndAncestors(tree: CategoryTreeNode[], targetId: string): void {
    const path: string[] = [];

    const findPath = (nodes: CategoryTreeNode[], currentPath: string[]): boolean => {
      for (const n of nodes) {
        const nextPath = [...currentPath, n.id];
        if (n.id === targetId) {
          path.push(...nextPath);
          return true;
        }
        if (n.children && n.children.length > 0) {
          if (findPath(n.children, nextPath)) {
            return true;
          }
        }
      }
      return false;
    };

    findPath(tree, []);
    if (path.length > 0) {
      this.expandedNodeKeys.update((keys) => {
        const updated = new Set(keys);
        path.forEach((id) => updated.add(id));
        return updated;
      });
    }
  }

  private countAllCategories(nodes: CategoryTreeNode[]): number {
    let count = 0;
    for (const n of nodes) {
      count += 1;
      if (n.children && n.children.length > 0) {
        count += this.countAllCategories(n.children);
      }
    }
    return count;
  }

  private countAllAssignedTables(nodes: CategoryTreeNode[]): number {
    let count = 0;
    for (const n of nodes) {
      count += n.assigned_tables_count || (n.tables ? n.tables.length : 0);
      if (n.children && n.children.length > 0) {
        count += this.countAllAssignedTables(n.children);
      }
    }
    return count;
  }

  // Node toggle event from TreeTable
  onNodeToggle(event: any): void {
    const node = event?.node || event;
    if (!node || !node.key) return;
    this.expandedNodeKeys.update((keys) => {
      const updated = new Set(keys);
      if (node.expanded) {
        updated.add(node.key!);
      } else {
        updated.delete(node.key!);
      }
      return updated;
    });
  }

  expandAll(): void {
    const allIds = new Set<string>();
    const collectIds = (nodes: CategoryTreeNode[]) => {
      for (const n of nodes) {
        allIds.add(n.id);
        if (n.children && n.children.length > 0) {
          collectIds(n.children);
        }
      }
    };
    collectIds(this.rawCategoryTree());
    this.expandedNodeKeys.set(allIds);
  }

  collapseAll(): void {
    this.expandedNodeKeys.set(new Set<string>());
  }

  // ==========================================
  // CREATE / EDIT CATEGORY MODAL
  // ==========================================

  openAddRootModal(): void {
    this.categoryModalMode.set('create-root');
    this.selectedParentCategory.set(null);
    this.editingCategory.set(null);
    this.categoryForm.reset({
      name: '',
      description: '',
      status: 'ACTIVE',
    });
    this.isCategoryModalOpen.set(true);
  }

  openAddSubcategoryModal(parent: CategoryRowData): void {
    this.categoryModalMode.set('create-child');
    this.selectedParentCategory.set(parent);
    this.editingCategory.set(null);
    this.categoryForm.reset({
      name: '',
      description: '',
      status: 'ACTIVE',
    });
    this.isCategoryModalOpen.set(true);
  }

  openEditCategoryModal(category: CategoryRowData): void {
    this.categoryModalMode.set('edit');
    this.selectedParentCategory.set(null);
    this.editingCategory.set(category);
    this.categoryForm.setValue({
      name: category.name,
      description: category.description || '',
      status: category.status,
    });
    this.isCategoryModalOpen.set(true);
  }

  closeCategoryModal(): void {
    this.isCategoryModalOpen.set(false);
    this.selectedParentCategory.set(null);
    this.editingCategory.set(null);
    this.categoryForm.reset();
  }

  submitCategoryForm(): void {
    if (this.categoryForm.invalid) {
      this.categoryForm.markAllAsTouched();
      return;
    }

    const { name, description, status } = this.categoryForm.getRawValue();
    this.isSavingCategory.set(true);

    const mode = this.categoryModalMode();

    if (mode === 'create-root') {
      this.categoryService
        .createCategory({
          name: name.trim(),
          description: description?.trim() || null,
          parent_id: null,
          status: status,
        })
        .subscribe({
          next: (created) => {
            this.isSavingCategory.set(false);
            this.closeCategoryModal();
            this.showToast(`دسته‌بندی اصلی «${created.name}» با موفقیت ایجاد شد`, 'success');
            this.loadCategoryTree(created.id);
          },
          error: (err) => {
            this.isSavingCategory.set(false);
            this.showToast(this.extractErrorMessage(err, 'خطا در ایجاد دسته‌بندی اصلی'), 'error');
          },
        });
    } else if (mode === 'create-child') {
      const parent = this.selectedParentCategory();
      if (!parent) return;

      this.categoryService
        .createCategory({
          name: name.trim(),
          description: description?.trim() || null,
          parent_id: parent.id,
          status: status,
        })
        .subscribe({
          next: (created) => {
            this.isSavingCategory.set(false);
            this.closeCategoryModal();
            this.showToast(
              `زیردسته «${created.name}» با موفقیت به «${parent.name}» افزوده شد`,
              'success'
            );
            // Automatically expand parent node
            this.expandedNodeKeys.update((keys) => {
              const updated = new Set(keys);
              updated.add(parent.id);
              return updated;
            });
            this.loadCategoryTree(created.id);
          },
          error: (err) => {
            this.isSavingCategory.set(false);
            this.showToast(this.extractErrorMessage(err, 'خطا در ایجاد زیردسته'), 'error');
          },
        });
    } else if (mode === 'edit') {
      const category = this.editingCategory();
      if (!category) return;

      this.categoryService
        .updateCategory(category.id, {
          name: name.trim(),
          description: description?.trim() || null,
          parent_id: category.parent_id || null,
          status: status,
        })
        .subscribe({
          next: (updated) => {
            this.isSavingCategory.set(false);
            this.closeCategoryModal();
            this.showToast(`تغییرات دسته‌بندی «${updated.name}» با موفقیت ذخیره شد`, 'success');
            this.loadCategoryTree(updated.id);
          },
          error: (err) => {
            this.isSavingCategory.set(false);
            this.showToast(this.extractErrorMessage(err, 'خطا در ویرایش دسته‌بندی'), 'error');
          },
        });
    }
  }

  // ==========================================
  // SAFE DELETION CONFIRMATION
  // ==========================================

  confirmDeleteCategory(category: CategoryRowData): void {
    this.categoryToDelete.set(category);

    // Calculate subcategory count
    const findNode = (nodes: CategoryTreeNode[], id: string): CategoryTreeNode | null => {
      for (const n of nodes) {
        if (n.id === id) return n;
        if (n.children) {
          const found = findNode(n.children, id);
          if (found) return found;
        }
      }
      return null;
    };

    const targetNode = findNode(this.rawCategoryTree(), category.id);
    const subCount = targetNode?.children?.length || 0;
    const tableCount = category.assigned_tables_count || 0;

    this.deleteSubcategoryCount.set(subCount);
    this.deleteTableCount.set(tableCount);
    this.isDeleteModalOpen.set(true);
  }

  closeDeleteModal(): void {
    this.isDeleteModalOpen.set(false);
    this.categoryToDelete.set(null);
  }

  executeDeleteCategory(): void {
    const cat = this.categoryToDelete();
    if (!cat) return;

    if (this.deleteSubcategoryCount() > 0 || this.deleteTableCount() > 0) {
      return;
    }

    this.categoryService.deleteCategory(cat.id).subscribe({
      next: () => {
        this.closeDeleteModal();
        this.showToast(`دسته‌بندی «${cat.name}» با موفقیت حذف شد`, 'success');
        this.loadCategoryTree();
      },
      error: (err) => {
        this.showToast(this.extractErrorMessage(err, 'امکان حذف این دسته‌بندی وجود ندارد'), 'error');
      },
    });
  }

  // ==========================================
  // ASSIGN TABLES MODAL (`اختصاص جداول`)
  // ==========================================

  openAssignTablesModal(category: CategoryRowData): void {
    this.targetCategoryForAssign.set(category);
    this.tableSearchQuery.set('');
    this.isAssignModalOpen.set(true);
    this.loadCategoryAssignments(category.id);
    this.searchAvailableTables('');
  }

  closeAssignModal(): void {
    this.isAssignModalOpen.set(false);
    this.targetCategoryForAssign.set(null);
    this.currentAssignedTables.set([]);
    this.availableCatalogTables.set([]);
  }

  loadCategoryAssignments(categoryId: string): void {
    this.categoryService.getAssignments(categoryId, 'TABLE').subscribe({
      next: (assignments) => {
        this.currentAssignedTables.set(assignments);
      },
      error: (err) => {
        this.showToast(this.extractErrorMessage(err, 'خطا در بارگذاری جداول اختصاص‌یافته'), 'error');
      },
    });
  }

  searchAvailableTables(query: string): void {
    this.isLoadingAvailableTables.set(true);
    this.categoryService.getAvailableTables(query).subscribe({
      next: (tables) => {
        this.availableCatalogTables.set(tables);
        this.isLoadingAvailableTables.set(false);
      },
      error: (err) => {
        this.isLoadingAvailableTables.set(false);
        this.showToast(this.extractErrorMessage(err, 'خطا در جستجوی جداول کاتالوگ'), 'error');
      },
    });
  }

  onTableSearchInput(event: Event): void {
    const input = event.target as HTMLInputElement;
    const query = input.value;
    this.tableSearchQuery.set(query);
    this.searchAvailableTables(query);
  }

  isTableAssigned(tableId: string): boolean {
    return this.currentAssignedTables().some((a) => a.entity_id === tableId);
  }

  assignTableToCurrentCategory(table: AvailableTableItem): void {
    const cat = this.targetCategoryForAssign();
    if (!cat) return;

    if (this.isTableAssigned(table.id)) {
      this.showToast(`جدول «${table.name}» پیش از این اختصاص یافته است`, 'error');
      return;
    }

    this.categoryService.assignTable(cat.id, table.id).subscribe({
      next: () => {
        this.showToast(`جدول «${table.name}» با موفقیت به دسته‌بندی اختصاص یافت`, 'success');
        this.loadCategoryAssignments(cat.id);
        this.loadCategoryTree(cat.id);
      },
      error: (err) => {
        this.showToast(this.extractErrorMessage(err, 'خطا در اختصاص جدول به دسته‌بندی'), 'error');
      },
    });
  }

  unassignTableFromCurrentCategory(assignment: CategoryAssignmentItem): void {
    const cat = this.targetCategoryForAssign();
    if (!cat) return;

    this.categoryService.unassignTable(cat.id, assignment.entity_id).subscribe({
      next: () => {
        this.showToast(`تخصیص جدول «${assignment.table_name || 'انتخاب شده'}» لغو شد`, 'success');
        this.loadCategoryAssignments(cat.id);
        this.loadCategoryTree(cat.id);
      },
      error: (err) => {
        this.showToast(this.extractErrorMessage(err, 'خطا در لغو تخصیص جدول'), 'error');
      },
    });
  }

  // ==========================================
  // GOVERNED TAGS METHODS (Preserved)
  // ==========================================

  loadTags(): void {
    this.isLoadingTags.set(true);
    this.http.get<TagItem[]>('/api/v1/tags').subscribe({
      next: (data) => {
        this.tags.set(data);
        this.isLoadingTags.set(false);
      },
      error: () => {
        this.isLoadingTags.set(false);
      },
    });
  }

  openCreateTagModal(): void {
    this.editingTagId.set(null);
    this.tagFormName = '';
    this.tagFormColor = '#3b82f6';
    this.tagFormDescription = '';
    this.isTagModalOpen.set(true);
  }

  openEditTagModal(tag: TagItem): void {
    this.editingTagId.set(tag.id);
    this.tagFormName = tag.name;
    this.tagFormColor = tag.color;
    this.tagFormDescription = tag.description || '';
    this.isTagModalOpen.set(true);
  }

  closeTagModal(): void {
    this.isTagModalOpen.set(false);
  }

  saveTag(): void {
    if (!this.tagFormName.trim()) return;

    const payload = {
      name: this.tagFormName.trim(),
      color: this.tagFormColor,
      description: this.tagFormDescription.trim() || null,
      is_governed: true,
    };

    const id = this.editingTagId();
    if (id) {
      this.http.put(`/api/v1/tags/${id}`, payload).subscribe({
        next: () => {
          this.closeTagModal();
          this.loadTags();
          this.showToast('برچسب با موفقیت ویرایش شد', 'success');
        },
        error: (err) => {
          this.showToast(this.extractErrorMessage(err, 'خطا در ویرایش برچسب'), 'error');
        },
      });
    } else {
      this.http.post('/api/v1/tags', payload).subscribe({
        next: () => {
          this.closeTagModal();
          this.loadTags();
          this.showToast('برچسب جدید با موفقیت ایجاد شد', 'success');
        },
        error: (err) => {
          this.showToast(this.extractErrorMessage(err, 'خطا در ایجاد برچسب'), 'error');
        },
      });
    }
  }

  deleteTag(tag: TagItem): void {
    if (!confirm(`آیا از حذف برچسب «${tag.name}» اطمینان دارید؟`)) return;
    this.http.delete(`/api/v1/tags/${tag.id}`).subscribe({
      next: () => {
        this.loadTags();
        this.showToast('برچسب حذف شد', 'success');
      },
      error: (err) => {
        this.showToast(this.extractErrorMessage(err, 'خطا در حذف برچسب'), 'error');
      },
    });
  }

  // ==========================================
  // HELPERS
  // ==========================================

  showToast(message: string, type: 'success' | 'error' = 'success'): void {
    if (this.toastTimer) {
      clearTimeout(this.toastTimer);
    }
    this.toastMessage.set(message);
    this.toastType.set(type);
    this.toastTimer = setTimeout(() => {
      this.toastMessage.set(null);
    }, 4500);
  }

  closeToast(): void {
    this.toastMessage.set(null);
    if (this.toastTimer) {
      clearTimeout(this.toastTimer);
    }
  }

  private extractErrorMessage(err: any, fallback: string): string {
    if (err?.error?.detail) {
      if (typeof err.error.detail === 'string') {
        return err.error.detail;
      }
      if (Array.isArray(err.error.detail) && err.error.detail[0]?.msg) {
        return err.error.detail[0].msg;
      }
    }
    return fallback;
  }
}
