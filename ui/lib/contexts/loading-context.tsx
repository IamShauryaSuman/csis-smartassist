"use client";

import { createContext, useCallback, useContext, useState } from "react";
import LoadingScreen from "@/components/layout/loading-screen";

interface LoadingContextType {
  setGlobalLoading: (isLoading: boolean, message?: string) => void;
}

const LoadingContext = createContext<LoadingContextType>({
  setGlobalLoading: () => {},
});

export function LoadingProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const setGlobalLoading = useCallback((isLoading: boolean, nextMessage?: string) => {
    setLoading(isLoading);
    if (nextMessage) setMessage(nextMessage);
  }, []);

  return (
    <LoadingContext.Provider
      value={{ setGlobalLoading }}
    >
      {children}
      {loading && <LoadingScreen message={message} />}
    </LoadingContext.Provider>
  );
}

export const useGlobalLoading = () => useContext(LoadingContext);
