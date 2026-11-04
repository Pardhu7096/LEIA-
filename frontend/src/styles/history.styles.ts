import { StyleSheet } from "react-native";

export const historyStyles = StyleSheet.create({
  layout: {
    flex: 1,
    flexDirection: "row",
    minHeight: 0,
  },
  main: {
    flex: 1,
    minWidth: 0,
    minHeight: 0,
  },
  sidebar: {
    width: 270,
    backgroundColor: "#F0F3EA",
    borderRightWidth: 1,
    borderRightColor: "#DFE5D8",
    paddingTop: 20,
  },
  heading: {
    paddingHorizontal: 20,
    marginBottom: 16,
    fontSize: 16,
    fontWeight: "700",
    color: "#294B3C",
  },
  list: {
    flex: 1,
  },
  listContent: {
    padding: 12,
    gap: 8,
  },
  item: {
    padding: 14,
    borderRadius: 12,
    backgroundColor: "#E5EBDD",
  },
  itemText: {
    color: "#294B3C",
    fontSize: 14,
    lineHeight: 20,
  },
  empty: {
    padding: 20,
    color: "#596D5F",
  },
  historyButton: {
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 10,
    backgroundColor: "#E8EEE1",
  },
  buttonText: {
    color: "#294B3C",
    fontWeight: "600",
  },
  drawer: {
    flex: 1,
    backgroundColor: "#F0F3EA",
  },
  drawerHeader: {
    padding: 20,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  drawerTitle: {
    fontSize: 20,
    fontWeight: "700",
    color: "#294B3C",
  },
});