import { Inter } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "../lib/auth";
import DashboardShell from "../components/DashboardShell";

const inter = Inter({ subsets: ["latin"] });

export const metadata = {
  title: "Adaptive Gateway Admin",
  description: "Admin Dashboard for Adaptive Gateway",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className={`${inter.className} antialiased min-h-screen`}>
        <AuthProvider>
          <DashboardShell>
            {children}
          </DashboardShell>
        </AuthProvider>
      </body>
    </html>
  );
}
