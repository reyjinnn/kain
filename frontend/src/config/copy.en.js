export const errors = {
  UNCONFIRMED_INPUT:
    "Measurements have not been checked. Review your fabric and select “Measurements are correct”.",
  UNSUPPORTED_CONSTRUCTION:
    "This size or shape is not supported yet. Choose measurements within SISA's limits.",
  AREA_LOWER_BOUND_FAIL:
    "There is not enough fabric for this size. Add fabric or change the measurements.",
  NO_LAYOUT_WITHIN_BUDGET:
    "No suitable layout was found. Try different fabric or allow a center seam.",
  STALE_REVISION:
    "Your fabric or requirements have changed. Create a new design before downloading.",
  CONSTRUCTION_UNREVIEWED:
    "The construction still needs review. Use the guide to make a paper test first.",
  AI_IMAGE_UNAVAILABLE:
    "The image could not be generated. The pattern is still available; you can try generating the image again.",
  INVALID_INPUT:
    "A measurement, shape, or defect marker needs correcting. Check your details.",
  AI_IMAGE_LIMIT: "The image allowance for this design has been used.",
  CHAT_LIMIT:
    "This session's chat limit has been reached. You can enter your requirements manually.",
  PROJECT_LIMIT: 'You have saved 20 projects. Delete an old save to add another project.',
  PROJECT_NOT_FOUND: 'This save no longer exists. Choose another project or save again.',
  PROJECT_INVALID: 'This save is not compatible with the current guide. Your details are still safe.',
  SAVE_FAILED: 'The save could not be written. Your details are still here; try again.',
};
export const explanationLabels = {
  summary: 'Summary', fabric_choice: 'Fabric choice', what_to_expect: 'Shape and seams',
  watch_out: 'What to check', limits: 'Guide limits',
};
export const tourCopy = [
  'Describe your pouch size and colors, then select Send. AI helps understand what you need.',
  'Measure the fabric and mark defects. Review your details before selecting Measurements are correct.',
  'Rotate the model to see other sides. Download the guide, then check the calibration box when printing.',
];
