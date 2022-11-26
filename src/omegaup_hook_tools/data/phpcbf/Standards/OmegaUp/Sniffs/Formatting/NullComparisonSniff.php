<?php

namespace PHP_CodeSniffer\Standards\OmegaUp\Sniffs\Formatting;

class NullComparisonSniff implements \PHP_CodeSniffer\Sniffs\Sniff {
    /**
     * Returns an array of tokens this test wants to listen for.
     *
     * @return array
     */
    public function register() {
        return [
            T_IS_EQUAL,
            T_IS_NOT_EQUAL,
            T_IS_IDENTICAL,
            T_IS_NOT_IDENTICAL,
        ];
    }

    /**
     * Processes this test, when one of its tokens is encountered.
     *
     * @param \PHP_CodeSniffer\Files\File $phpcsFile The file being scanned.
     * @param int                         $stackPtr  The position of the current token in
     *                                               the stack passed in $tokens.
     *
     * @return void
     */
    public function process(\PHP_CodeSniffer\Files\File $phpcsFile, $stackPtr) {
        $tokens    = $phpcsFile->getTokens();
        $errorData = [strtolower($tokens[$stackPtr]['content'])];

        $nullValue = $phpcsFile->findNext(
            T_WHITESPACE,
            $stackPtr + 1,
            null,
            true
        );
        if ($tokens[$nullValue]['code'] !== T_NULL) {
            return;
        }

        if (
            $tokens[$stackPtr]['code'] == T_IS_EQUAL
            || $tokens[$stackPtr]['code'] === T_IS_IDENTICAL
        ) {
            $phpcsFile->addError(
                'Disallowed comparison against null. Use is_null() instead',
                $stackPtr,
                'NullComparison',
                $errorData
            );
        } else {
            $phpcsFile->addError(
                'Disallowed comparison against null. Use !is_null() instead',
                $stackPtr,
                'NullComparison',
                $errorData
            );
        }
    }
}
