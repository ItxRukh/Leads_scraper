import { createContext, useContext } from "react";

// What every component can read: the latest /connections answer and a way to refresh it.
export const ConnectionsContext = createContext({ data: null, refresh: () => {} });

export const useConnections = () => useContext(ConnectionsContext);
