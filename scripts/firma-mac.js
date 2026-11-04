// electron-builder passa a codesign il nome del certificato. Con due Developer ID dallo stesso nome nel portachiavi
// codesign rifiuta il nome come ambiguo: qui si firma con l'impronta indicata in CSC_NAME.
exports.default = async function firma(opzioni) {
  const { signAsync } = await import("@electron/osx-sign");
  await signAsync({ ...opzioni, identity: process.env.CSC_NAME });
};
