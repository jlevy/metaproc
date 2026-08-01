type MetabrowserPerf = {
  measure<T>(name: string, fn: () => T, metadata?: Record<string, unknown>): T;
  measureAsync<T>(
    name: string,
    fn: () => Promise<T>,
    metadata?: Record<string, unknown>,
  ): Promise<T>;
};

type MetabrowserRenderer = (container: HTMLElement, context: object) => unknown;

type MetabrowserSdk = {
  builtins: {
    agentLog?: {
      renderLog: MetabrowserRenderer;
      renderRaw: MetabrowserRenderer;
    };
    markdown?: {
      renderRendered: MetabrowserRenderer;
      renderSource: MetabrowserRenderer;
    };
  };
  escapeHtml(value: unknown): string;
  fetchPluginData(
    plugin: string,
    endpoint: string,
    parameters: Record<string, unknown>,
  ): Promise<unknown>;
  formatSize(value: number): string;
  openPath(path: string): void;
  perf: MetabrowserPerf;
  registerView(
    kind: string,
    view: string,
    spec: { dispose?: () => void; render: CallableFunction },
  ): void;
  render(template: string, values: object): string;
  wrapWithCopy(html: string): string;
};

type MetabrowserIconRegistry = {
  [name: string]: string | CallableFunction | undefined;
  toggle?: string;
  withClass?(name: string, className: string): string;
};

type MetaprocVisualizationRuntime = Record<string, unknown> & {
  renderViz(container: HTMLElement, visualization: unknown, options: object): Promise<void>;
};

type MetaprocVizData = {
  viz?: {
    header?: { name?: string };
    nodes?: Array<{
      id?: string;
      kind?: string;
      label?: string;
      step?: { mode?: string };
    }>;
  };
};

type MetaprocDomainViewRuntime = {
  [name: string]: CallableFunction;
  clearPluginDataRequests(): void;
  disposeCharts(): void;
  loadCharts(container: HTMLElement, path: string, isCurrent: () => boolean): Promise<void>;
  loadStats(container: HTMLElement, path: string, isCurrent: () => boolean): Promise<void>;
  loadVisual(container: HTMLElement, path: string, isCurrent: () => boolean): Promise<void>;
  loadVizModel(path: string): Promise<MetaprocVizData>;
  renderResourceReportPayload(payload: unknown, metricKey: string): string;
  renderResourceTreemapPayload(payload: unknown, metricKey: string): string;
  setCurrentResourceReportPayload(payload: unknown, path: string): unknown;
};

declare global {
  interface Window {
    ELK?: new () => {
      layout(input: object): Promise<unknown>;
    };
    MetabrowserCharts?: {
      dispose(): void;
      renderPayload(container: HTMLElement, chartData: unknown): unknown;
    };
    MetabrowserFileTypes?: {
      classFor(path: string): string;
    };
    MetabrowserIcons?: MetabrowserIconRegistry;
    MetabrowserTooltip?: {
      hide(): void;
      move(event: MouseEvent): void;
      show(html: string, event: MouseEvent): void;
    };
    MetaprocDomainViews: MetaprocDomainViewRuntime;
    MetaprocResourceSelection?: {
      get(): string | null;
      set(nodeId: string | null, sourceTag: string): void;
      subscribe(callback: CallableFunction): () => void;
    };
    MetaprocViz: MetaprocVisualizationRuntime;
    metabrowser: MetabrowserSdk;
  }
}

export {};
