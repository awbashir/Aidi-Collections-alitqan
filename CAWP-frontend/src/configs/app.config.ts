export type AppConfig = {
  apiPrefix: string;
  authenticatedEntryPath: string;
  unAuthenticatedEntryPath: string;
  tourPath: string;
  locale: string;
  enableMock: boolean;
};
//"http://192.168.100.224:8080"
//"http://10.10.10.240:8080"
//"https://10.217.170.200:8080"
//"http://127.0.0.1:9797"

const appConfig: AppConfig = {
  // Set VITE_API_URL in .env.development / .env.production; falls back to the LAN deployment.
  apiPrefix: (import.meta.env.VITE_API_URL as string | undefined) ?? "http://10.10.10.240:8080",
  authenticatedEntryPath: "/home",
  unAuthenticatedEntryPath: "/sign-in",
  tourPath: "/",
  locale: "ar",
  enableMock: false,
};

export default appConfig;
