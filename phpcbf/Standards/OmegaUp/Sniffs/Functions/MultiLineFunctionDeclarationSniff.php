<?php

namespace PHP_CodeSniffer\Standards\OmegaUp\Sniffs\Functions;

class MultiLineFunctionDeclarationSniff extends \PHP_CodeSniffer\Standards\Squiz\Sniffs\Functions\MultiLineFunctionDeclarationSniff {
    /**
     * Determine if this is a multi-line function declaration.
     *
     * @param \PHP_CodeSniffer\Files\File $phpcsFile   The file being scanned.
     * @param int                         $stackPtr    The position of the
     *                                                 current token in the
     *                                                 stack passed in $tokens.
     * @param int                         $openBracket The position of the
     *                                                 opening bracket in the
     *                                                 stack passed in $tokens.
     * @param array                       $tokens      The stack of tokens that
     *                                                 make up the file.
     *
     * @return bool
     */
    public function isMultiLineDeclaration(
        $phpcsFile,
        $stackPtr,
        $openBracket,
        $tokens
    ) {
        if (
            parent::isMultiLineDeclaration(
                $phpcsFile,
                $stackPtr,
                $openBracket,
                $tokens
            )
        ) {
            return true;
        }

        $bracketsToCheck = [$stackPtr => $openBracket];

        // Closures may use the USE keyword and so be multi-line in this way.
        if ($tokens[$stackPtr]['code'] === T_CLOSURE) {
            $use = $phpcsFile->findNext(
                T_USE,
                ($tokens[$openBracket]['parenthesis_closer'] + 1),
                $tokens[$stackPtr]['scope_opener']
            );
            if ($use !== false) {
                $open = $phpcsFile->findNext(T_OPEN_PARENTHESIS, ($use + 1));
                if ($open !== false) {
                    $bracketsToCheck[$use] = $open;
                }
            }
        }

        foreach ($bracketsToCheck as $stackPtr => $openBracket) {
            $closeBracket = $tokens[$openBracket]['parenthesis_closer'];
        }

        // If the parameter list is too large, it needs to be broken down in
        // multiple lines.
        $next = $phpcsFile->findNext(
            T_OPEN_CURLY_BRACKET,
            ($closeBracket + 1)
        );
        return $tokens[$next]['column'] > 80;
    }
}
