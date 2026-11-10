"use strict";
/**
 * @license
 * Copyright Google LLC All Rights Reserved.
 *
 * Use of this source code is governed by an MIT-style license that can be
 * found in the LICENSE file at https://angular.dev/license
 */
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.replaceBootstrap = replaceBootstrap;
exports.elideImports = elideImports;
const typescript_1 = __importDefault(require("typescript"));
/**
 * The name of the Angular platform that should be replaced within
 * bootstrap call expressions to support AOT.
 */
const PLATFORM_BROWSER_DYNAMIC_NAME = 'platformBrowserDynamic';
function replaceBootstrap(getTypeChecker) {
    return (context) => {
        let bootstrapImport;
        let bootstrapNamespace;
        const replacedNodes = [];
        const nodeFactory = context.factory;
        const visitNode = (node) => {
            if (typescript_1.default.isCallExpression(node) && typescript_1.default.isIdentifier(node.expression)) {
                const target = node.expression;
                if (target.text === PLATFORM_BROWSER_DYNAMIC_NAME) {
                    if (!bootstrapNamespace) {
                        bootstrapNamespace = nodeFactory.createUniqueName('__NgCli_bootstrap_');
                        bootstrapImport = nodeFactory.createImportDeclaration(undefined, nodeFactory.createImportClause(undefined, undefined, nodeFactory.createNamespaceImport(bootstrapNamespace)), nodeFactory.createStringLiteral('@angular/platform-browser'));
                    }
                    replacedNodes.push(target);
                    return nodeFactory.updateCallExpression(node, nodeFactory.createPropertyAccessExpression(bootstrapNamespace, 'platformBrowser'), node.typeArguments, node.arguments);
                }
            }
            return typescript_1.default.visitEachChild(node, visitNode, context);
        };
        return (sourceFile) => {
            if (!sourceFile.text.includes(PLATFORM_BROWSER_DYNAMIC_NAME)) {
                return sourceFile;
            }
            let updatedSourceFile = typescript_1.default.visitEachChild(sourceFile, visitNode, context);
            if (bootstrapImport) {
                // Remove any unused platform browser dynamic imports
                const removals = elideImports(updatedSourceFile, replacedNodes, getTypeChecker, context.getCompilerOptions());
                if (removals.size > 0) {
                    updatedSourceFile = typescript_1.default.visitEachChild(updatedSourceFile, function visitForRemoval(node) {
                        if (removals.has(node)) {
                            return undefined;
                        }
                        return typescript_1.default.isImportDeclaration(node) ||
                            typescript_1.default.isImportClause(node) ||
                            typescript_1.default.isNamedImports(node)
                            ? typescript_1.default.visitEachChild(node, visitForRemoval, context)
                            : node;
                    }, context);
                }
                // Add new platform browser import
                return nodeFactory.updateSourceFile(updatedSourceFile, typescript_1.default.setTextRange(nodeFactory.createNodeArray([bootstrapImport, ...updatedSourceFile.statements]), sourceFile.statements));
            }
            else {
                return updatedSourceFile;
            }
        };
    };
}
// Remove imports for which all identifiers have been removed.
// Needs type checker, and works even if it's not the first transformer.
// Works by removing imports for symbols whose identifiers have all been removed.
// Doesn't use the `symbol.declarations` because that previous transforms might have removed nodes
// but the type checker doesn't know.
// See https://github.com/Microsoft/TypeScript/issues/17552 for more information.
function elideImports(sourceFile, removedNodes, getTypeChecker, compilerOptions) {
    const importNodeRemovals = new Set();
    if (removedNodes.length === 0) {
        return importNodeRemovals;
    }
    const typeChecker = getTypeChecker();
    // Collect all imports and used identifiers
    const usedSymbols = new Set();
    const imports = [];
    typescript_1.default.forEachChild(sourceFile, function visit(node) {
        // Skip removed nodes.
        if (removedNodes.includes(node)) {
            return;
        }
        // Consider types for 'implements' as unused.
        // A HeritageClause token can also be an 'AbstractKeyword'
        // which in that case we should not elide the import.
        if (typescript_1.default.isHeritageClause(node) && node.token === typescript_1.default.SyntaxKind.ImplementsKeyword) {
            return;
        }
        // Record import and skip
        if (typescript_1.default.isImportDeclaration(node)) {
            if (!node.importClause?.isTypeOnly) {
                imports.push(node);
            }
            return;
        }
        // Type nodes do not need to be emitted when `emitDecoratorMetadata` is disabled.
        // `ExpressionWithTypeArguments` is excluded as it also represents runtime values such as
        // `class ... extends Base<T>` heritage clauses and instantiation expressions (`Map<string, T>`).
        if (typescript_1.default.isTypeNode(node) &&
            !typescript_1.default.isExpressionWithTypeArguments(node) &&
            !compilerOptions.emitDecoratorMetadata) {
            return;
        }
        // Pure type declarations, type queries, ambient statements, external re-exports,
        // and type-only exports are erased or do not reference local imports at runtime.
        if (typescript_1.default.isInterfaceDeclaration(node) ||
            typescript_1.default.isTypeAliasDeclaration(node) ||
            typescript_1.default.isTypeParameterDeclaration(node) ||
            typescript_1.default.isTypeQueryNode(node) ||
            typescript_1.default.isImportTypeNode(node) ||
            typescript_1.default.isIndexSignatureDeclaration(node) ||
            (typescript_1.default.isStatement(node) &&
                typescript_1.default.canHaveModifiers(node) &&
                typescript_1.default.getModifiers(node)?.some((m) => m.kind === typescript_1.default.SyntaxKind.DeclareKeyword)) ||
            (typescript_1.default.isExportDeclaration(node) && (node.isTypeOnly || node.moduleSpecifier !== undefined)) ||
            (typescript_1.default.isExportSpecifier(node) && node.isTypeOnly)) {
            return;
        }
        let symbol;
        switch (node.kind) {
            case typescript_1.default.SyntaxKind.Identifier:
                if (node.parent && typescript_1.default.isShorthandPropertyAssignment(node.parent)) {
                    const shorthandSymbol = typeChecker.getShorthandAssignmentValueSymbol(node.parent);
                    if (shorthandSymbol) {
                        symbol = shorthandSymbol;
                    }
                }
                else {
                    symbol = typeChecker.getSymbolAtLocation(node);
                }
                break;
            case typescript_1.default.SyntaxKind.ExportSpecifier:
                symbol = typeChecker.getExportSpecifierLocalTargetSymbol(node);
                break;
            case typescript_1.default.SyntaxKind.ShorthandPropertyAssignment:
                symbol = typeChecker.getShorthandAssignmentValueSymbol(node);
                break;
        }
        if (symbol) {
            usedSymbols.add(symbol);
        }
        typescript_1.default.forEachChild(node, visit);
    });
    if (imports.length === 0) {
        return importNodeRemovals;
    }
    /**
     * Determines how an imported binding should be handled:
     * - `keep`: the binding is still referenced.
     * - `value`: the binding is a runtime value that is no longer referenced.
     * - `type`: the binding does not exist at runtime and is always elided by TypeScript.
     */
    const getRemovalKind = (node) => {
        // Do not remove JSX factory imports
        if (node.text === compilerOptions.jsxFactory) {
            return 'keep';
        }
        const symbol = typeChecker.getSymbolAtLocation(node);
        if (!symbol) {
            return 'keep';
        }
        // Note: with `verbatimModuleSyntax`, TypeScript reports an error for imports of non-value aliases
        // that do not use a `type` modifier (TS1484/TS1485), so no special handling is needed for it.
        if ((symbol.flags & typescript_1.default.SymbolFlags.Alias) !== 0 && !isValueAlias(symbol, typeChecker)) {
            return 'type';
        }
        return usedSymbols.has(symbol) ? 'keep' : 'value';
    };
    for (const node of imports) {
        if (!node.importClause) {
            // "import 'abc';"
            continue;
        }
        const namedBindings = node.importClause.namedBindings;
        if (namedBindings && typescript_1.default.isNamespaceImport(namedBindings)) {
            // "import * as XYZ from 'abc';"
            if (getRemovalKind(namedBindings.name) !== 'keep') {
                importNodeRemovals.add(node);
            }
        }
        else {
            const specifierNodeRemovals = [];
            let clausesCount = 0;
            // TypeScript skips its own import elision for declarations replaced by a transformer.
            // A declaration is therefore only partially rewritten when a runtime value import must be
            // removed, or when it was already replaced by a previous transformer. Otherwise, it is left
            // untouched so that TypeScript continues to elide any type-only specifiers itself.
            let requiresRewrite = !typescript_1.default.isParseTreeNode(node);
            // "import { XYZ, ... } from 'abc';"
            if (namedBindings && typescript_1.default.isNamedImports(namedBindings)) {
                let removedClausesCount = 0;
                clausesCount += namedBindings.elements.length;
                for (const specifier of namedBindings.elements) {
                    const removalKind = specifier.isTypeOnly ? 'type' : getRemovalKind(specifier.name);
                    if (removalKind !== 'keep') {
                        requiresRewrite ||= removalKind === 'value';
                        removedClausesCount++;
                        // in case we don't have any more namedImports we should remove the parent ie the {}
                        const nodeToRemove = clausesCount === removedClausesCount ? specifier.parent : specifier;
                        specifierNodeRemovals.push(nodeToRemove);
                    }
                }
            }
            // "import XYZ from 'abc';"
            if (node.importClause.name) {
                clausesCount++;
                const removalKind = node.importClause.isTypeOnly
                    ? 'type'
                    : getRemovalKind(node.importClause.name);
                if (removalKind !== 'keep') {
                    requiresRewrite ||= removalKind === 'value';
                    specifierNodeRemovals.push(node.importClause.name);
                }
            }
            if (specifierNodeRemovals.length === clausesCount) {
                importNodeRemovals.add(node);
            }
            else if (requiresRewrite) {
                for (const specifierNodeRemoval of specifierNodeRemovals) {
                    importNodeRemovals.add(specifierNodeRemoval);
                }
            }
        }
    }
    return importNodeRemovals;
}
/**
 * Determines whether an import alias resolves to a runtime value. Aliases that only resolve to
 * types, or that pass through a type-only import or export declaration anywhere along the alias
 * chain, are always elided by TypeScript.
 */
function isValueAlias(symbol, typeChecker) {
    const seen = new Set();
    let current = symbol;
    while ((current.flags & typescript_1.default.SymbolFlags.Alias) !== 0) {
        if (seen.has(current)) {
            // Circular alias (a TypeScript error) — assume runtime value so we don't over-elide.
            return true;
        }
        seen.add(current);
        if (current.declarations?.length &&
            current.declarations.every(typescript_1.default.isTypeOnlyImportOrExportDeclaration)) {
            return false;
        }
        const next = typeChecker.getImmediateAliasedSymbol(current);
        if (!next) {
            // Unresolved alias (e.g., missing module) — assume runtime value so we don't over-elide.
            return true;
        }
        current = next;
    }
    return (current.flags & typescript_1.default.SymbolFlags.Value) !== 0;
}
//# sourceMappingURL=jit-bootstrap-transformer.js.map