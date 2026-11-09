import { StyleSheet } from "react-native";

export const tourStyles = StyleSheet.create({
  button: {
    marginTop: 16,
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 12,
    backgroundColor: "#E8EEE1",
    alignItems: "center",
  },
  buttonText: {
    color: "#294B3C",
    fontSize: 14,
    fontWeight: "600",
  },
  screen: {
    flex: 1,
    backgroundColor: "#FFFDF8",
  },
  header: {
    padding: 20,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
  },
  title: {
    fontSize: 18,
    fontWeight: "700",
    color: "#294B3C",
  },
  propertySummary: {
    flex: 1,
    minWidth: 0,
    gap: 4,
  },
  summaryDetails: {
    color: "#64765B",
    fontSize: 13,
  },
  propertyTabs: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    paddingHorizontal: 20,
    paddingBottom: 16,
  },
  selectedTab: {
    backgroundColor: "#D0E0C7",
  },
  viewerArea: {
    flex: 1,
  },
  hidden: {
    display: "none",
  },
  close: {
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: 10,
    backgroundColor: "#E8EEE1",
  },
  closeText: {
    color: "#294B3C",
    fontWeight: "600",
  },
  notice: {
    paddingHorizontal: 20,
    paddingBottom: 16,
    color: "#596D5F",
    fontSize: 13,
  },
  viewer: {
    flex: 1,
    overflow: "hidden",
    backgroundColor: "#15251D",
  },
  help: {
    padding: 16,
    textAlign: "center",
    color: "#596D5F",
    fontSize: 13,
  },
});
