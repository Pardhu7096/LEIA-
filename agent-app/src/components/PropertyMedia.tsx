import { Text } from "react-native";

import type { StaffFetch } from "../lib/staffSession";

export type MediaProps = {
  propertyId: string;
  version: number;
  authFetch: StaffFetch;
  onBusyChange: (busy: boolean) => void;
  editable?: boolean;
};

export default function PropertyMedia(_: MediaProps) {
  return <Text>Open the LEIA Agent Workspace in your browser to manage or review photos.</Text>;
}
