import { StyleSheet } from "react-native";

export const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: "#FAFBF8",
  },

  header: {
    padding: 20,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    borderBottomWidth: 1,
    borderBottomColor: "#E3E8DF",
  },

  brand: {
    fontSize: 25,
    fontWeight: "700",
    color: "#294638",
  },

  newSearchButton: {
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: "#E8EEE1",
  },

  newSearchText: {
    color: "#416C54",
    fontSize: 13,
    fontWeight: "600",
  },

  scroll: {
    flex: 1,
  },

  content: {
    width: "100%",
    maxWidth: 900,
    alignSelf: "center",
    padding: 20,
    paddingBottom: 40,
  },

  welcome: {
    paddingVertical: 80,
    alignItems: "center",
  },

  eyebrow: {
    color: "#718269",
    fontSize: 10,
    letterSpacing: 2,
    textAlign: "center",
    marginBottom: 20,
  },

  heading: {
    fontSize: 36,
    fontWeight: "600",
    color: "#294638",
    textAlign: "center",
    marginBottom: 15,
  },

  subtitle: {
    color: "#768174",
    fontSize: 14,
    lineHeight: 23,
  },

  messageGroup: {
    marginBottom: 20,
  },

  assistantLabel: {
    color: "#416C54",
    fontSize: 12,
    fontWeight: "700",
    marginBottom: 8,
  },

  message: {
    padding: 16,
    borderRadius: 16,
    maxWidth: "85%",
  },

  userMessage: {
    backgroundColor: "#E8EEE1",
    alignSelf: "flex-end",
  },

  assistantMessage: {
    backgroundColor: "#F0F3EB",
    alignSelf: "flex-start",
  },

  messageText: {
    color: "#294638",
    fontSize: 15,
    lineHeight: 23,
  },

  cards: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 18,
    marginTop: 18,
  },

  card: {
    width: "100%",
    maxWidth: 350,
    backgroundColor: "#FFFFFF",
    borderRadius: 16,
    overflow: "hidden",
    borderWidth: 1,
    borderColor: "#E3E8DF",
  },

  photo: {
    width: "100%",
    height: 200,
    resizeMode: "cover",
  },

  cardBody: {
    padding: 18,
  },

  sample: {
    fontSize: 10,
    color: "#7B8972",
    letterSpacing: 1,
  },

  price: {
    fontSize: 25,
    fontWeight: "700",
    color: "#294638",
    marginVertical: 10,
  },

  title: {
    fontSize: 16,
    fontWeight: "600",
    color: "#294638",
    marginBottom: 4,
  },

  details: {
    color: "#64765B",
    marginTop: 14,
    fontSize: 13,
  },

  bottom: {
    padding: 16,
    width: "100%",
    maxWidth: 900,
    alignSelf: "center",
  },

  composer: {
    flexDirection: "row",
    backgroundColor: "#FFFFFF",
    borderWidth: 1,
    borderColor: "#DCE4D5",
    borderRadius: 16,
    padding: 8,
    alignItems: "center",
  },

  input: {
    flex: 1,
    minWidth: 0,
    padding: 12,
    color: "#294638",
    fontSize: 15,
  },

  button: {
    backgroundColor: "#416C54",
    borderRadius: 10,
    padding: 14,
  },

  buttonDisabled: {
    opacity: 0.5,
  },

  buttonText: {
    color: "#FFFFFF",
    fontWeight: "600",
  },

  disclaimer: {
    textAlign: "center",
    color: "#879180",
    fontSize: 10,
    lineHeight: 16,
    marginTop: 10,
  },
});