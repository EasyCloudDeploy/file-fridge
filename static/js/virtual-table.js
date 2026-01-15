/**
 * VirtualTable - High-performance virtual scrolling table for large datasets
 * Only renders visible rows to minimize DOM elements and memory usage.
 */
class VirtualTable {
    constructor(options) {
        this.container = options.container;           // Scrollable container element
        this.tbody = options.tbody;                   // Table tbody element
        this.rowHeight = options.rowHeight || 48;     // Fixed row height in pixels
        this.bufferSize = options.bufferSize || 5;    // Extra rows above/below viewport
        this.renderRow = options.renderRow;           // Function to render a row: (file, index) => HTMLElement
        this.onNearEnd = options.onNearEnd || null;   // Callback when near end of data (for infinite scroll)
        this.nearEndThreshold = options.nearEndThreshold || 200; // Pixels from bottom to trigger onNearEnd

        this.data = [];                               // All file data
        this.visibleRows = new Map();                 // Map of index -> row element
        this.spacer = null;                           // Spacer element for scroll height
        this.scrollTop = 0;
        this.viewportHeight = 0;
        this.ticking = false;
        this.lastRenderedRange = { start: -1, end: -1 };

        this._init();
    }

    _init() {
        // Create spacer element for maintaining scroll height
        this.spacer = document.createElement('div');
        this.spacer.className = 'virtual-table-spacer';
        this.spacer.style.cssText = 'position: relative; width: 100%; pointer-events: none;';
        this.tbody.appendChild(this.spacer);

        // Create row container
        this.rowContainer = document.createElement('div');
        this.rowContainer.className = 'virtual-table-rows';
        this.rowContainer.style.cssText = 'position: absolute; top: 0; left: 0; right: 0;';
        this.tbody.appendChild(this.rowContainer);

        // Set tbody to relative positioning
        this.tbody.style.position = 'relative';
        this.tbody.style.display = 'block';

        // Get viewport dimensions
        this._updateViewportHeight();

        // Bind scroll handler with RAF throttling
        this._boundScrollHandler = this._onScroll.bind(this);
        this.container.addEventListener('scroll', this._boundScrollHandler, { passive: true });

        // Handle resize
        this._boundResizeHandler = this._onResize.bind(this);
        window.addEventListener('resize', this._boundResizeHandler, { passive: true });
    }

    _updateViewportHeight() {
        this.viewportHeight = this.container.clientHeight;
    }

    _onScroll() {
        if (!this.ticking) {
            requestAnimationFrame(() => {
                this.scrollTop = this.container.scrollTop;
                this.render();
                this._checkNearEnd();
                this.ticking = false;
            });
            this.ticking = true;
        }
    }

    _onResize() {
        this._updateViewportHeight();
        this.render();
    }

    _checkNearEnd() {
        if (this.onNearEnd) {
            const scrollBottom = this.scrollTop + this.viewportHeight;
            const totalHeight = this.data.length * this.rowHeight;
            if (totalHeight - scrollBottom < this.nearEndThreshold) {
                this.onNearEnd();
            }
        }
    }

    /**
     * Calculate which row indices are visible
     */
    getVisibleRange() {
        const startIndex = Math.floor(this.scrollTop / this.rowHeight) - this.bufferSize;
        const visibleCount = Math.ceil(this.viewportHeight / this.rowHeight);
        const endIndex = startIndex + visibleCount + (this.bufferSize * 2);

        return {
            start: Math.max(0, startIndex),
            end: Math.min(this.data.length - 1, endIndex)
        };
    }

    /**
     * Render visible rows
     */
    render() {
        if (this.data.length === 0) {
            return;
        }

        const { start, end } = this.getVisibleRange();

        // Skip if range hasn't changed
        if (start === this.lastRenderedRange.start && end === this.lastRenderedRange.end) {
            return;
        }
        this.lastRenderedRange = { start, end };

        // Remove rows outside visible range
        for (const [index, row] of this.visibleRows) {
            if (index < start || index > end) {
                row.remove();
                this.visibleRows.delete(index);
            }
        }

        // Add missing rows in visible range
        const fragment = document.createDocumentFragment();
        for (let i = start; i <= end; i++) {
            if (!this.visibleRows.has(i) && this.data[i]) {
                const row = this._createRow(this.data[i], i);
                this.visibleRows.set(i, row);
                fragment.appendChild(row);
            }
        }

        if (fragment.childNodes.length > 0) {
            this.rowContainer.appendChild(fragment);
        }
    }

    /**
     * Create a row element with proper positioning
     */
    _createRow(file, index) {
        const row = this.renderRow(file, index);
        row.style.position = 'absolute';
        row.style.top = `${index * this.rowHeight}px`;
        row.style.left = '0';
        row.style.right = '0';
        row.style.height = `${this.rowHeight}px`;
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.dataset.index = index;
        return row;
    }

    /**
     * Update spacer height to match total content
     */
    _updateSpacerHeight() {
        const totalHeight = this.data.length * this.rowHeight;
        this.spacer.style.height = `${totalHeight}px`;
    }

    /**
     * Set all data at once (replaces existing data)
     */
    setData(files) {
        this.data = files;
        this._updateSpacerHeight();
        this.clear();
        this.scrollTop = this.container.scrollTop;
        this.render();
    }

    /**
     * Append a single file to the data array
     */
    appendData(file) {
        this.data.push(file);
        this._updateSpacerHeight();

        // Only re-render if new item might be visible
        const { end } = this.getVisibleRange();
        if (this.data.length - 1 <= end + this.bufferSize) {
            this.lastRenderedRange = { start: -1, end: -1 }; // Force re-render
            this.render();
        }
    }

    /**
     * Append multiple files at once
     */
    appendBatch(files) {
        const startIndex = this.data.length;
        this.data.push(...files);
        this._updateSpacerHeight();

        // Check if any new items are in visible range
        const { end } = this.getVisibleRange();
        if (startIndex <= end + this.bufferSize) {
            this.lastRenderedRange = { start: -1, end: -1 };
            this.render();
        }
    }

    /**
     * Clear all rendered rows
     */
    clear() {
        this.rowContainer.innerHTML = '';
        this.visibleRows.clear();
        this.lastRenderedRange = { start: -1, end: -1 };
    }

    /**
     * Reset table completely (clear data and DOM)
     */
    reset() {
        this.data = [];
        this.clear();
        this._updateSpacerHeight();
    }

    /**
     * Get current data count
     */
    getCount() {
        return this.data.length;
    }

    /**
     * Get file at specific index
     */
    getItem(index) {
        return this.data[index];
    }

    /**
     * Scroll to specific row index
     */
    scrollToIndex(index) {
        const targetTop = index * this.rowHeight;
        this.container.scrollTop = targetTop;
    }

    /**
     * Update a specific row's data and re-render if visible
     */
    updateItem(index, newData) {
        if (index >= 0 && index < this.data.length) {
            this.data[index] = newData;

            // Re-render if visible
            if (this.visibleRows.has(index)) {
                const oldRow = this.visibleRows.get(index);
                const newRow = this._createRow(newData, index);
                oldRow.replaceWith(newRow);
                this.visibleRows.set(index, newRow);
            }
        }
    }

    /**
     * Cleanup when done
     */
    destroy() {
        this.container.removeEventListener('scroll', this._boundScrollHandler);
        window.removeEventListener('resize', this._boundResizeHandler);
        this.reset();
    }
}

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = VirtualTable;
}
