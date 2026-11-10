"use strict";
/**
 * @license
 * Copyright Google LLC All Rights Reserved.
 *
 * Use of this source code is governed by an MIT-style license that can be
 * found in the LICENSE file at https://angular.dev/license
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.SOURCEMAP_COMMENT_BYTES = exports.SOURCEMAP_COMMENT_PREFIX = void 0;
exports.findTrailingSourceMapComment = findTrailingSourceMapComment;
exports.removeSourceMappingURL = removeSourceMappingURL;
exports.isTrailingSourceMapComment = isTrailingSourceMapComment;
exports.loadInputSourceMapFromUrl = loadInputSourceMapFromUrl;
exports.loadInputSourceMap = loadInputSourceMap;
const node_fs_1 = require("node:fs");
const node_module_1 = require("node:module");
const node_path_1 = require("node:path");
const node_url_1 = require("node:url");
exports.SOURCEMAP_COMMENT_PREFIX = '//# sourceMappingURL=';
exports.SOURCEMAP_COMMENT_BYTES = Buffer.from(exports.SOURCEMAP_COMMENT_PREFIX);
const SOURCEMAP_COMMENT_VALUE_PREFIX = exports.SOURCEMAP_COMMENT_PREFIX.slice(2);
let parseSync;
/**
 * Checks for a single trailing `//# sourceMappingURL=` comment on a raw buffer.
 *
 * @param data The raw byte buffer to inspect.
 * @returns An object containing the sliced code buffer and URL snippet if a single trailing comment exists,
 *          `null` if no sourcemap comment exists in the buffer, or `undefined` if multiple/non-trailing comments exist.
 */
function findTrailingSourceMapComment(data) {
    const dataBuffer = Buffer.isBuffer(data)
        ? data
        : Buffer.from(data.buffer, data.byteOffset, data.byteLength);
    const firstIndex = dataBuffer.indexOf(exports.SOURCEMAP_COMMENT_BYTES);
    if (firstIndex === -1) {
        return null;
    }
    const lastIndex = dataBuffer.lastIndexOf(exports.SOURCEMAP_COMMENT_BYTES);
    // Skip any preceding horizontal whitespace (spaces/tabs) to find the start of the line.
    let prevIdx = lastIndex - 1;
    while (prevIdx >= 0 && (dataBuffer[prevIdx] === 32 || dataBuffer[prevIdx] === 9)) {
        prevIdx--;
    }
    // Ensure the comment starts at the beginning of a line or the start of the file,
    // preventing false positives for occurrences inside inline string literals or code.
    const isLineStart = prevIdx < 0 || dataBuffer[prevIdx] === 10 || dataBuffer[prevIdx] === 13;
    if (firstIndex === lastIndex && isLineStart) {
        const urlLine = dataBuffer
            .subarray(lastIndex + exports.SOURCEMAP_COMMENT_BYTES.length)
            .toString('utf-8');
        return {
            code: dataBuffer.subarray(0, prevIdx < 0 ? 0 : prevIdx + 1),
            urlLine,
        };
    }
    return undefined;
}
function removeSourceMappingURL(code) {
    if (typeof code === 'string') {
        return removeSourceMappingURLFromString(code);
    }
    const trailing = findTrailingSourceMapComment(code);
    if (trailing === null) {
        return code;
    }
    if (trailing && isTrailingSourceMapComment(trailing.urlLine)) {
        return trailing.code;
    }
    // Fallback to full decode and AST lexer comment stripping for multiple or non-trailing comments
    const dataBuffer = Buffer.isBuffer(code)
        ? code
        : Buffer.from(code.buffer, code.byteOffset, code.byteLength);
    const text = dataBuffer.toString('utf-8');
    const stripped = removeSourceMappingURLFromString(text, true);
    return stripped === text ? code : Buffer.from(stripped, 'utf-8');
}
function removeSourceMappingURLFromString(code, skipTrailingCheck = false) {
    const firstIndex = code.indexOf(exports.SOURCEMAP_COMMENT_PREFIX);
    if (firstIndex === -1) {
        return code;
    }
    if (!skipTrailingCheck) {
        const lastIndex = code.lastIndexOf(exports.SOURCEMAP_COMMENT_PREFIX);
        let prevIdx = lastIndex - 1;
        while (prevIdx >= 0 && (code[prevIdx] === ' ' || code[prevIdx] === '\t')) {
            prevIdx--;
        }
        const isLineStart = prevIdx < 0 || code[prevIdx] === '\n' || code[prevIdx] === '\r';
        if (firstIndex === lastIndex &&
            isLineStart &&
            isTrailingSourceMapComment(code.slice(lastIndex + exports.SOURCEMAP_COMMENT_PREFIX.length))) {
            return code.slice(0, prevIdx < 0 ? 0 : prevIdx + 1);
        }
    }
    if (!parseSync) {
        const localRequire = (0, node_module_1.createRequire)(__filename);
        parseSync = localRequire('oxc-parser').parseSync;
    }
    const { comments } = parseSync('source.js', code, {
        lang: 'js',
        sourceType: 'unambiguous',
    });
    const result = [];
    let lastCopiedIndex = 0;
    for (const comment of comments) {
        if (comment.type !== 'Line' || !comment.value.startsWith(SOURCEMAP_COMMENT_VALUE_PREFIX)) {
            continue;
        }
        let start = comment.start;
        let end = comment.end;
        // Preserve line terminators on non-trailing comments so subsequent line numbers stay aligned
        // with already-generated sourcemaps (and ASI is unaffected when comments follow code). When a
        // comment is the only content on its line, strip leading horizontal whitespace—and if only
        // whitespace follows to EOF, strip to the end of the file to match the trailing fast path.
        let lineStart = start;
        while (lineStart > lastCopiedIndex && isHorizontalWhitespace(code.charCodeAt(lineStart - 1))) {
            lineStart--;
        }
        if (lineStart === 0 || isLineTerminator(code.charCodeAt(lineStart - 1))) {
            start = lineStart;
            if (isWhitespaceToEnd(code, end)) {
                end = code.length;
            }
        }
        if (start > lastCopiedIndex) {
            result.push(code.slice(lastCopiedIndex, start));
        }
        lastCopiedIndex = end;
    }
    if (lastCopiedIndex === 0) {
        return code;
    }
    if (lastCopiedIndex < code.length) {
        result.push(code.slice(lastCopiedIndex));
    }
    return result.join('');
}
function isHorizontalWhitespace(code) {
    return code === 32 || code === 9; // space, tab
}
function isLineTerminator(code) {
    return code === 10 || code === 13; // \n, \r
}
function isWhitespaceToEnd(code, start) {
    for (let i = start; i < code.length; i++) {
        const char = code.charCodeAt(i);
        if (!isHorizontalWhitespace(char) && !isLineTerminator(char)) {
            return false;
        }
    }
    return true;
}
function isBase64Char(code) {
    return ((code >= 65 && code <= 90) || // A-Z
        (code >= 97 && code <= 122) || // a-z
        (code >= 48 && code <= 57) || // 0-9
        code === 43 || // +
        code === 47 || // /
        code === 61 || // =
        code === 45 || // -
        code === 95 // _
    );
}
/**
 * Extracts the base64 payload from an inline sourcemap data URI line and verifies
 * that only trailing whitespace follows the payload.
 *
 * @returns The base64 payload string if valid and trailing, or `undefined` otherwise.
 */
function extractTrailingBase64Payload(urlLine) {
    if (!urlLine.startsWith('data:application/json;')) {
        return undefined;
    }
    const base64StartIndex = urlLine.indexOf('base64,');
    if (base64StartIndex === -1) {
        return undefined;
    }
    const payloadStart = base64StartIndex + 7;
    let payloadEnd = urlLine.length;
    // Find the first trailing whitespace character that marks the end of the base64 payload,
    // while ensuring all payload characters are valid base64 (preventing false positives when
    // closing quotes, backticks, or `*/` immediately follow the payload).
    for (let i = payloadStart; i < urlLine.length; i++) {
        const char = urlLine[i];
        if (char === ' ' || char === '\r' || char === '\n' || char === '\t') {
            payloadEnd = i;
            break;
        }
        if (!isBase64Char(urlLine.charCodeAt(i))) {
            return undefined;
        }
    }
    if (payloadEnd === payloadStart) {
        return undefined;
    }
    // Verify that everything after the base64 payload is trailing whitespace
    // to ensure this is a valid trailing sourceMappingURL comment at the end of the file.
    for (let i = payloadEnd; i < urlLine.length; i++) {
        const char = urlLine[i];
        if (char !== ' ' && char !== '\r' && char !== '\n' && char !== '\t') {
            return undefined;
        }
    }
    return urlLine.slice(payloadStart, payloadEnd);
}
/**
 * Extracts the URL from an external sourcemap comment line and verifies
 * that only trailing whitespace follows the URL.
 *
 * @returns The URL string if valid and trailing, or `undefined` otherwise.
 */
function extractTrailingUrl(urlLine) {
    if (urlLine.startsWith('data:')) {
        return undefined;
    }
    const urlMatch = /^([^\r\n\s'"`]+)/.exec(urlLine);
    if (!urlMatch || urlMatch[1].includes('*/')) {
        return undefined;
    }
    const remaining = urlLine.slice(urlMatch[1].length);
    if (!/^\s*$/.test(remaining)) {
        return undefined;
    }
    return urlMatch[1];
}
/**
 * Checks whether a `//# sourceMappingURL=` URL line snippet represents a valid trailing comment at the end of the file.
 */
function isTrailingSourceMapComment(urlLine) {
    return (extractTrailingBase64Payload(urlLine) !== undefined || extractTrailingUrl(urlLine) !== undefined);
}
/**
 * Resolves and loads the input sourcemap referenced in a `//# sourceMappingURL=` URL line snippet.
 * Supports inline base64 data URIs, local absolute file URLs, and relative/absolute filesystem paths.
 */
function loadInputSourceMapFromUrl(filename, urlLine) {
    // Inline base64-encoded sourcemaps can be extremely large (up to megabytes).
    // Parse them without regular expressions to avoid heavy backtracking and allocations.
    const base64Payload = extractTrailingBase64Payload(urlLine);
    if (base64Payload !== undefined) {
        try {
            return JSON.parse(Buffer.from(base64Payload, 'base64').toString('utf-8'));
        }
        catch {
            return undefined;
        }
    }
    const url = extractTrailingUrl(urlLine);
    if (!url) {
        return undefined;
    }
    if (url.startsWith('file://')) {
        // Local absolute file URL scheme.
        try {
            const mapPath = (0, node_url_1.fileURLToPath)(url);
            if ((0, node_fs_1.existsSync)(mapPath)) {
                return JSON.parse((0, node_fs_1.readFileSync)(mapPath, 'utf8'));
            }
        }
        catch { }
    }
    else if (!/^[a-z]+:\/\//i.test(url)) {
        // Local relative or absolute filesystem path (percent-decoded as it originates from a URI).
        try {
            const mapPath = (0, node_path_1.resolve)((0, node_path_1.dirname)(filename), decodeURIComponent(url));
            if ((0, node_fs_1.existsSync)(mapPath)) {
                return JSON.parse((0, node_fs_1.readFileSync)(mapPath, 'utf8'));
            }
        }
        catch { }
    }
    return undefined;
}
/**
 * Finds, resolves, and loads the input sourcemap referenced in the code's trailing
 * sourceMappingURL comment, if present. Supports inline base64 data URIs, local absolute
 * file URLs, and relative/absolute filesystem paths.
 */
function loadInputSourceMap(filename, code) {
    // Locate the last sourceMappingURL comment using lastIndexOf to avoid scanning
    // the entire file with a regular expression (significant for large files).
    const lastSourceMapIndex = code.lastIndexOf(exports.SOURCEMAP_COMMENT_PREFIX);
    if (lastSourceMapIndex === -1) {
        return undefined;
    }
    if (lastSourceMapIndex > 0) {
        // Skip any preceding horizontal whitespace (spaces/tabs) to find the start of the line.
        let prevIdx = lastSourceMapIndex - 1;
        while (prevIdx >= 0 && (code[prevIdx] === ' ' || code[prevIdx] === '\t')) {
            prevIdx--;
        }
        // Ensure the comment starts at the beginning of a line, preventing false positives within code or strings.
        if (prevIdx >= 0 && code[prevIdx] !== '\n' && code[prevIdx] !== '\r') {
            return undefined;
        }
    }
    return loadInputSourceMapFromUrl(filename, code.slice(lastSourceMapIndex + exports.SOURCEMAP_COMMENT_PREFIX.length));
}
//# sourceMappingURL=source-map.js.map