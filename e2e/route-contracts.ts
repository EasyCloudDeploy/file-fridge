export type RouteContext = {
    primaryPathId: number | null;
    remoteConnectionId: number | null;
};

export type RouteContract = {
    name: string;
    path: string | ((context: RouteContext) => string | null);
    scripts: string[];
    expectedApis: string[];
    loadingSelectors: string[];
    readySelectors: string[];
    emptySelector?: string;
    errorSelector?: string;
};

export const routeContracts: RouteContract[] = [
    {
        name: 'dashboard',
        path: '/',
        scripts: ['/static/js/dashboard.js', '/static/js/storage.js'],
        expectedApis: ['/health', '/api/v1/stats', '/api/v1/paths', '/api/v1/paths/stats', '/api/v1/storage/stats'],
        loadingSelectors: [
            '#pathsList .spinner-border',
            '#recentFilesList .spinner-border',
            '#hotStorageStatusList .spinner-border',
            '#storageStatusList .spinner-border',
        ],
        readySelectors: ['#pathsCount', '#recentCount', '#app-version'],
    },
    {
        name: 'paths-list',
        path: '/paths',
        scripts: ['/static/js/paths.js'],
        expectedApis: ['/api/v1/paths', '/health'],
        loadingSelectors: ['#paths-loading'],
        readySelectors: ['#paths-content', '#no-paths-message'],
        emptySelector: '#no-paths-message',
    },
    {
        name: 'path-detail',
        path: (context) => (context.primaryPathId ? `/paths/${context.primaryPathId}` : null),
        scripts: ['/static/js/paths.js'],
        expectedApis: ['/api/v1/paths/', '/api/v1/criteria/path/', '/api/v1/storage/stats', '/api/v1/paths/stats'],
        loadingSelectors: ['#path-loading'],
        readySelectors: ['#path-content', '#path-error'],
        errorSelector: '#path-error',
    },
    {
        name: 'storage-locations',
        path: '/storage-locations',
        scripts: ['/static/js/storage-locations.js'],
        expectedApis: ['/api/v1/storage/locations', '/health'],
        loadingSelectors: ['#locations-loading'],
        readySelectors: ['#locations-content', '#no-locations-message'],
        emptySelector: '#no-locations-message',
    },
    {
        name: 'files',
        path: '/files',
        scripts: ['/static/js/files.js'],
        expectedApis: ['/api/v1/files', '/api/v1/paths', '/api/v1/tags', '/api/v1/storage/locations', '/health'],
        loadingSelectors: ['#files-loading'],
        readySelectors: ['#filesGrid', '#no-files-message'],
        emptySelector: '#no-files-message',
    },
    {
        name: 'tags',
        path: '/tags',
        scripts: ['/static/js/tags.js'],
        expectedApis: ['/api/v1/tags', '/api/v1/tag-rules', '/health'],
        loadingSelectors: ['#tags_loading', '#rules_loading'],
        readySelectors: ['#tags_content', '#tags_empty', '#rules_content', '#rules_empty'],
        emptySelector: '#tags_empty',
        errorSelector: '#tags_loading .alert, #rules_loading .alert',
    },
    {
        name: 'notifiers',
        path: '/notifiers',
        scripts: ['/static/js/notifiers.js'],
        expectedApis: ['/api/v1/notifiers', '/health'],
        loadingSelectors: ['#notifiers_loading'],
        readySelectors: ['#notifiers_content', '#notifiers_empty'],
        emptySelector: '#notifiers_empty',
    },
    {
        name: 'stats',
        path: '/stats',
        scripts: ['/static/js/stats.js'],
        expectedApis: ['/api/v1/stats/detailed', '/health'],
        loadingSelectors: [
            '#daily-chart-loading .spinner-border',
            '#storage-chart-loading .spinner-border',
            '#top-files-chart-loading .spinner-border',
            '#top-size-chart-loading .spinner-border',
        ],
        readySelectors: ['#dailyChart', '#storageChart', '#topPathsByFilesChart', '#topPathsBySizeChart'],
        errorSelector:
            '#daily-chart-loading .alert, #storage-chart-loading .alert, #top-files-chart-loading .alert, #top-size-chart-loading .alert',
    },
    {
        name: 'settings',
        path: '/settings',
        scripts: ['/static/js/settings.js'],
        expectedApis: ['/health'],
        loadingSelectors: [],
        readySelectors: ['#accounts-section'],
    },
    {
        name: 'migrations',
        path: '/migrations',
        scripts: [],
        expectedApis: ['/api/v1/migrations/active', '/api/v1/migrations/freezing', '/api/v1/migrations/recent'],
        loadingSelectors: ['tbody output.spinner-border'],
        readySelectors: [],
    },
    {
        name: 'remote-files',
        path: (context) =>
            context.remoteConnectionId ? `/remote-files/${context.remoteConnectionId}` : null,
        scripts: ['/static/js/remote_files.js'],
        expectedApis: ['/api/v1/remote/connections/', '/api/v1/paths/monitored'],
        loadingSelectors: ['.ag-overlay-loading-center'],
        readySelectors: ['#remote-instance-name', '#filesGrid'],
    },
];
